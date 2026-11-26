"""Cuts handed to an editor: the two files that say where every clip comes from."""

import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from constructive_media import ffmpeg, ingest
from constructive_media.edit import EditError, export, timelines
from constructive_media.edit.export import Source
from constructive_media.edit.timelines import Clip

from . import test_cli
from .support import HAVE_FFMPEG, TEST_DATABASE_VARIABLE, DatabaseTest, make_still, make_video, settings_in
from .test_render import over

FILM = Fraction(24000, 1001)
VIDEO = Fraction(30000, 1001)

#: Two cameras, one of them numbering its frames from one o'clock, a sound
#: recorder started at one o'clock, and a photograph.
QUAY = Source("quay", Path("/made-up/day one/quay.mov"), "video", 25.0, 60.0, True, 1920, 1080,
              timecode="01:00:00:00")
BOATS = Source("boats", Path("/made-up/day one/boats.mov"), "video", 29.97, 30.0, True, 3840, 2160,
               timecode="00:59:59;28")
MUTE = Source("mute", Path("/made-up/mute.mp4"), "video", 25.0, 12.0, False, 1920, 1080)
LAPEL = Source("lapel", Path("/made-up/day one/lapel #2.wav"), "audio", None, 90.0, True,
               after_midnight=Fraction(172_800_000, 48_000))
CHART = Source("chart", Path("/made-up/chart.png"), "image", None, None, False, 800, 600)
SHELF = {each.asset_id: each for each in (QUAY, BOATS, MUTE, LAPEL, CHART)}


class TimecodeTest(unittest.TestCase):
    def test_a_timecode_is_a_count_of_frames(self):
        self.assertEqual(export.frames_of("00:00:01:00", Fraction(25)), 25)
        self.assertEqual(export.frames_of("10:20:30:12", Fraction(25)), 930_762)
        self.assertEqual(export.frames_of("01:00:00:00", FILM), 86_400)
        # Counted thirty to the second and never put right, an hour is 108000 frames.
        self.assertEqual(export.frames_of("01:00:00:00", VIDEO), 108_000)
        self.assertEqual(export.timecode_of(930_762, Fraction(25)), "10:20:30:12")
        self.assertEqual(export.timecode_of(108_000, VIDEO), "01:00:00:00")
        self.assertEqual(export.timecode_of(-4, Fraction(25)), "00:00:00:00")
        self.assertEqual(export.timecode_of(25 * 86_400 + 3, Fraction(25)), "00:00:00:03")

    def test_drop_frame_leaves_out_two_numbers_a_minute_but_every_tenth(self):
        self.assertEqual(export.frames_of("00:00:59;29", VIDEO), 1799)
        self.assertEqual(export.frames_of("00:01:00;02", VIDEO), 1800)
        self.assertEqual(export.frames_of("00:10:00;00", VIDEO), 17_982)
        self.assertEqual(export.frames_of("01:00:00;00", VIDEO), 107_892)
        self.assertEqual(export.timecode_of(1799, VIDEO, drop=True), "00:00:59;29")
        self.assertEqual(export.timecode_of(1800, VIDEO, drop=True), "00:01:00;02")
        self.assertEqual(export.timecode_of(17_982, VIDEO, drop=True), "00:10:00;00")
        self.assertEqual(export.timecode_of(107_892, VIDEO, drop=True), "01:00:00;00")
        # At twice the rate, four.
        self.assertEqual(export.frames_of("00:01:00;04", Fraction(60000, 1001)), 3600)
        self.assertEqual(export.timecode_of(3600, Fraction(60000, 1001), drop=True), "00:01:00;04")

    def test_every_frame_has_one_timecode_and_comes_back_from_it(self):
        for per_s in (VIDEO, Fraction(60000, 1001)):
            for frame in list(range(0, 40_000, 7)) + list(range(1_000_000, 1_040_000, 11)):
                written = export.timecode_of(frame, per_s, drop=True)
                self.assertEqual(export.frames_of(written, per_s), frame, written)

    def test_only_the_rates_that_fall_behind_drop(self):
        self.assertTrue(export.dropping("01:00:00;00", VIDEO))
        self.assertFalse(export.dropping("01:00:00:00", VIDEO))
        self.assertFalse(export.dropping("01:00:00;00", Fraction(25)))
        self.assertFalse(export.dropping("01:00:00;00", FILM))
        self.assertFalse(export.dropping(None, VIDEO))
        self.assertEqual(export.frames_of("00:01:00;02", Fraction(25)), 1502)
        self.assertEqual(export.timecode_of(1502, Fraction(25), drop=True), "00:01:00:02")

    def test_what_is_not_a_timecode_is_refused(self):
        for written in ("", "01:00:00", "one o'clock", "01:00:00:00:00", "01:00:-1:00"):
            with self.assertRaisesRegex(EditError, "is not a timecode"):
                export.frames_of(written, Fraction(25))


class SourceTest(unittest.TestCase):
    def test_a_recording_is_timed_on_its_own_clock(self):
        self.assertEqual(QUAY.begins(VIDEO), 3600)
        self.assertEqual(QUAY.at(2.0, VIDEO), 3602)
        # To the nearest of its own frames, whatever the cut runs at.
        self.assertEqual(QUAY.at(2.03, VIDEO), 3602 + Fraction(1, 25))
        self.assertEqual(MUTE.begins(Fraction(25)), 0)
        self.assertEqual(MUTE.at(1.6, Fraction(25)), Fraction(8, 5))
        self.assertEqual(CHART.at(0.0, Fraction(25)), 0)

    def test_a_dropping_clock_reads_the_true_time(self):
        # Two frames before the hour by its numbers is 107890 frames of 1001/30000 of a second.
        self.assertEqual(BOATS.begins(Fraction(25)), Fraction(107_890 * 1001, 30_000))
        self.assertEqual(BOATS.at(1.0, Fraction(25)), Fraction(107_920 * 1001, 30_000))

    def test_a_sound_recording_starts_so_long_after_midnight(self):
        self.assertEqual(LAPEL.begins(Fraction(25)), 3600)
        # It has no frames of its own, so the cut's are used.
        self.assertEqual(LAPEL.at(6.01, Fraction(25)), 3606)
        self.assertEqual(LAPEL.at(6.03, Fraction(25)), 3606 + Fraction(1, 25))

    def test_a_clock_that_cannot_be_read_is_taken_as_none(self):
        odd = Source("odd", Path("/made-up/odd.mov"), "video", 25.0, timecode="about noon")
        self.assertEqual(odd.at(1.0, Fraction(25)), 1)
        # A camera is timed by its frames, never by a sound clock, and the other way about.
        self.assertEqual(Source("a", Path("/made-up/a.wav"), "audio", timecode="01:00:00:00").begins(VIDEO), 0)
        self.assertEqual(Source("b", Path("/made-up/b.mov"), "video", 25.0, after_midnight=Fraction(9)).begins(VIDEO), 0)


def cut():
    """A talk seen on one camera, then another over the lapel microphone, then a chart."""
    return [Clip("quay", 10.0, 14.0, label='She said "the tide & the boats"'),
            over("boats", 3.0, 5.48, "lapel", 21.0),
            Clip("chart", 0.0, 2.0),
            Clip("mute", 1.0, 2.0)]


class LaidTest(unittest.TestCase):
    def test_clips_are_placed_in_frames_from_the_start(self):
        events = export.laid(cut(), SHELF, Fraction(25))
        self.assertEqual([(each.number, each.at, each.frames) for each in events],
                         [(1, 0, 100), (2, 100, 62), (3, 162, 50), (4, 212, 25)])
        self.assertEqual([each.picture.asset_id for each in events], ["quay", "boats", "chart", "mute"])
        self.assertEqual([(each.sound.asset_id, each.sound_s) if each.sound else None for each in events],
                         [None, ("lapel", 21.0), None, None])
        self.assertEqual([each.name for each in events],
                         ['She said "the tide & the boats"', "boats", "chart", "mute"])

    def test_rounding_is_never_carried_forward(self):
        clips = [Clip("boats", 0.37 * n, 0.37 * (n + 1)) for n in range(40)]
        events = export.laid(clips, SHELF, VIDEO)
        self.assertEqual(events[-1].at + events[-1].frames, round(14.8 * VIDEO))
        for placed in events:
            self.assertLessEqual(abs(placed.at / VIDEO - 0.37 * (placed.number - 1)), 0.5 / VIDEO)
            self.assertIn(placed.frames, (11, 12))

    def test_a_clip_too_short_for_a_frame_is_left_out(self):
        events = export.laid([Clip("quay", 0.0, 0.015), Clip("quay", 5.0, 5.015), Clip("quay", 9.0, 10.0)],
                             SHELF, Fraction(25))
        self.assertEqual([(each.number, each.at, each.frames) for each in events], [(2, 0, 1), (3, 1, 25)])

    def test_sound_is_apart_when_it_is_not_what_is_under_the_picture(self):
        same = Clip("quay", 10.0, 14.0, sound="quay", sound_start_s=10.0, sound_end_s=14.0)
        later = Clip("quay", 10.0, 14.0, sound="quay", sound_start_s=30.0, sound_end_s=34.0)
        first, second = export.laid([same, later], SHELF, Fraction(25))
        self.assertIsNone(first.sound)
        self.assertEqual((second.sound, second.sound_s), (QUAY, 30.0))

    def test_a_long_name_is_cut_short(self):
        (placed,) = export.laid([Clip("quay", 0.0, 1.0, label="so  it\ngoes " * 12)], SHELF, Fraction(25))
        self.assertIn(len(placed.name), (69, 70))
        self.assertTrue(placed.name.startswith("so it goes so it goes"))
        self.assertTrue(placed.name.endswith("…"))


class ListTest(unittest.TestCase):
    def test_the_list_gives_every_event_on_its_recordings_clock(self):
        written = export.edl("The  quay\n", export.laid(cut(), SHELF, Fraction(25)), Fraction(25))
        self.assertEqual(written, "\n".join([
            "TITLE: The quay",
            "FCM: NON-DROP FRAME",
            "",
            "001  AX       AA/V  C        01:00:10:00 01:00:14:00 00:00:00:00 00:00:04:00",
            "* FROM CLIP NAME: quay.mov",
            "* SOURCE FILE: /made-up/day one/quay.mov",
            '* COMMENT: She said "the tide & the boats"',
            "",
            # Seventy-four frames of one camera for sixty-two of the cut's, on a clock that drops.
            "002  AX       V     C        01:00:02;28 01:00:05;12 00:00:04:00 00:00:06:12",
            "* FROM CLIP NAME: boats.mov",
            "* SOURCE FILE: /made-up/day one/boats.mov",
            "003  AX       A     C        01:00:21:00 01:00:23:12 00:00:04:00 00:00:06:12",
            "* FROM CLIP NAME: lapel #2.wav",
            "* SOURCE FILE: /made-up/day one/lapel #2.wav",
            "",
            "004  AX       V     C        00:00:00:00 00:00:02:00 00:00:06:12 00:00:08:12",
            "* FROM CLIP NAME: chart.png",
            "* SOURCE FILE: /made-up/chart.png",
            "",
            "005  AX       V     C        00:00:01:00 00:00:02:00 00:00:08:12 00:00:09:12",
            "* FROM CLIP NAME: mute.mp4",
            "* SOURCE FILE: /made-up/mute.mp4",
            "",
            "",
        ]))


def seconds(written):
    """An FCPXML time as a fraction of seconds."""
    assert written.endswith("s"), written
    return Fraction(written[:-1])


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.written = export.fcpxml('The "quay" & after', export.laid(cut(), SHELF, Fraction(25)), Fraction(25))
        self.root = ElementTree.fromstring(self.written)
        self.assets = {each.get("name"): each for each in self.root.iter("asset")}
        self.formats = {each.get("id"): each for each in self.root.iter("format")}

    def test_it_is_a_document_an_editor_will_open(self):
        self.assertTrue(self.written.startswith(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n'))
        self.assertEqual(self.root.find("library/event/project").get("name"), 'The "quay" & after')
        ids = [each.get("id") for each in self.root.find("resources")]
        self.assertEqual(len(ids), len(set(ids)))
        for clip in list(self.root.iter("asset-clip")) + list(self.root.iter("video")):
            self.assertIn(clip.get("ref"), [asset.get("id") for asset in self.assets.values()])

    def test_every_recording_is_named_where_it_is_with_what_it_has(self):
        self.assertEqual(sorted(self.assets), ["boats.mov", "chart.png", "lapel #2.wav", "mute.mp4", "quay.mov"])
        self.assertEqual({name: asset.find("media-rep").get("src") for name, asset in self.assets.items()}, {
            "quay.mov": "file:///made-up/day%20one/quay.mov",
            "boats.mov": "file:///made-up/day%20one/boats.mov",
            "lapel #2.wav": "file:///made-up/day%20one/lapel%20%232.wav",
            "chart.png": "file:///made-up/chart.png",
            "mute.mp4": "file:///made-up/mute.mp4",
        })
        self.assertEqual({name: (asset.get("hasVideo"), asset.get("hasAudio")) for name, asset in self.assets.items()}, {
            "quay.mov": ("1", "1"), "boats.mov": ("1", "1"), "lapel #2.wav": (None, "1"),
            "chart.png": ("1", None), "mute.mp4": ("1", None),
        })
        self.assertEqual({name: (seconds(asset.get("start")), asset.get("duration"))
                          for name, asset in self.assets.items()}, {
            # Thirty seconds is 899 of its frames, which is a little less.
            "quay.mov": (3600, "60s"), "boats.mov": (Fraction(107_890 * 1001, 30_000), "899899/30000s"),
            "lapel #2.wav": (3600, "90s"), "chart.png": (0, "0s"), "mute.mp4": (0, "12s"),
        })
        self.assertIsNone(self.assets["lapel #2.wav"].get("format"))

    def test_each_shape_and_rate_is_described_once(self):
        described = {(each.get("frameDuration"), each.get("width"), each.get("height"), each.get("name"))
                     for each in self.formats.values()}
        self.assertEqual(described, {
            ("1/25s", "1920", "1080", None), ("1001/30000s", "3840", "2160", None),
            (None, "800", "600", "FFVideoFormatRateUndefined"),
        })
        # The cut is the shape and rate of what is on screen longest, which both the plain cameras are.
        sequence = self.root.find("library/event/project/sequence")
        self.assertEqual(sequence.get("format"), self.assets["quay.mov"].get("format"))
        self.assertEqual(self.assets["mute.mp4"].get("format"), self.assets["quay.mov"].get("format"))
        self.assertEqual((sequence.get("duration"), sequence.get("tcFormat")), ("237/25s", "NDF"))

    def test_the_clips_follow_one_another_each_from_its_own_time(self):
        first, second, third, fourth = self.root.find("library/event/project/sequence/spine")
        self.assertEqual([each.tag for each in (first, second, third, fourth)],
                         ["asset-clip", "asset-clip", "video", "asset-clip"])
        self.assertEqual([(each.get("offset"), each.get("duration")) for each in (first, second, third, fourth)],
                         [("0s", "4s"), ("4s", "62/25s"), ("162/25s", "2s"), ("212/25s", "1s")])
        self.assertEqual((first.get("name"), first.get("start"), first.get("srcEnable")),
                         ('She said "the tide & the boats"', "3610s", None))
        self.assertEqual(seconds(second.get("start")), Fraction(107_980 * 1001, 30_000))
        self.assertEqual((third.get("start"), fourth.get("start")), ("0s", "1s"))
        self.assertEqual(len(first) + len(third) + len(fourth), 0)

    def test_sound_from_elsewhere_hangs_under_its_picture_and_the_cameras_own_is_off(self):
        second = self.root.find("library/event/project/sequence/spine")[1]
        (heard,) = second
        self.assertEqual(second.get("srcEnable"), "video")
        self.assertEqual((heard.tag, heard.get("ref"), heard.get("lane"), heard.get("audioRole")),
                         ("asset-clip", self.assets["lapel #2.wav"].get("id"), "-1", "dialogue"))
        # Placed on its parent's clock, and read from its own.
        self.assertEqual(heard.get("offset"), second.get("start"))
        self.assertEqual((heard.get("start"), heard.get("duration"), heard.get("srcEnable")),
                         ("3621s", "62/25s", None))

    def test_sound_taken_from_a_camera_is_only_its_sound(self):
        clips = [over("mute", 0.0, 2.0, "quay", 30.0), over("chart", 0.0, 1.0, "quay", 32.0)]
        root = ElementTree.fromstring(export.fcpxml("made-up", export.laid(clips, SHELF, FILM), FILM))
        first, second = root.find("library/event/project/sequence/spine")
        # Nothing to switch off: one has no sound of its own and the other is a photograph.
        self.assertEqual((first.get("srcEnable"), second.get("srcEnable"), second.tag), (None, None, "video"))
        self.assertEqual([each[0].get("srcEnable") for each in (first, second)], ["audio", "audio"])
        self.assertEqual([each[0].get("start") for each in (first, second)], ["3630s", "3632s"])
        self.assertEqual(first.get("duration"), "1001/500s")
        self.assertEqual(root.find("resources")[0].get("frameDuration"), "1001/24000s")

    def test_a_cut_of_recordings_of_no_known_size_is_given_a_usual_one(self):
        unsized = Source("unsized", Path("/made-up/unsized.mov"), "video", 25.0, 5.0)
        events = export.laid([Clip("unsized", 0.0, 1.0)], {"unsized": unsized}, Fraction(25))
        root = ElementTree.fromstring(export.fcpxml("made-up", events, Fraction(25)))
        shapes = {each.get("id"): (each.get("width"), each.get("height")) for each in root.iter("format")}
        self.assertEqual(shapes[root.find("library/event/project/sequence").get("format")], ("1920", "1080"))
        self.assertEqual(shapes[root.find("resources/asset").get("format")], (None, None))


class ManifestTest(unittest.TestCase):
    def manifest(self, clips, per_s=Fraction(25), **facts):
        events = export.laid(clips, SHELF, per_s)
        used = {source.asset_id: source for placed in events for source in (placed.picture, placed.sound) if source}
        made = export.Exported(
            Path("/made-up/out"), Path("/made-up/out/quay.fcpxml"), Path("/made-up/out/quay.edl"),
            Path("/made-up/out/README.md"), len(events), len(used),
            float(Fraction(events[-1].at + events[-1].frames) / per_s), per_s,
            tuple(sorted({source.rate for source in used.values() if source.rate})), **facts)
        return export.manifest("The quay", "quay", events, made)

    def test_the_page_says_what_was_written_and_what_it_points_at(self):
        page = self.manifest(cut())
        self.assertIn("# The quay\n\n4 clips, 0:09.5, from 5 recordings, at 25 frames a second.\n", page)
        self.assertIn("- `quay.fcpxml` is the whole cut", page)
        self.assertIn("- `quay.edl` is the same cut as a CMX3600 list", page)
        self.assertIn("\n- `/made-up`\n- `/made-up/day one`\n", page)
        self.assertIn("All 5 were there when this was written.", page)
        self.assertIn("3 of the 5 carry a clock of their own", page)
        self.assertEqual(page.split("## The clips\n\n")[1].splitlines()[2:], [
            '| 1 | 0:00.0 | quay.mov | 0:10.0 | quay.mov | 0:10.0 | She said "the tide & the boats" |',
            "| 2 | 0:04.0 | boats.mov | 0:03.0 | lapel #2.wav | 0:21.0 |  |",
            "| 3 | 0:06.5 | chart.png | 0:00.0 | | |  |",
            "| 4 | 0:08.5 | mute.mp4 | 0:01.0 | | |  |",
        ])

    def test_a_cut_of_mixed_rates_says_so(self):
        self.assertIn("**The recordings were shot at 25, 29.97 frames a second.** The cut is written at 25",
                      self.manifest(cut()))
        self.assertIn("Every recording is 25 frames a second, and so is the cut.",
                      self.manifest([Clip("quay", 0.0, 1.0), Clip("mute", 0.0, 1.0)]))

    def test_what_is_missing_and_what_was_left_out_are_named(self):
        page = self.manifest([Clip("quay", 0.0, 1.0)], missing=(Path("/made-up/day one/quay.mov"),), left_out=(2, 5))
        self.assertIn("**1 of them were not there when this was written,**", page)
        self.assertIn("\n- `/made-up/day one/quay.mov`\n", page)
        self.assertNotIn("were there when", page)
        self.assertIn("Too short for a single frame, and left out: clip 2, 5.", page)


def make_camera(path, timecode, seconds=3.0, rate="25"):
    """A video that numbers its first frame as a camera does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.encode(
        "-f", "lavfi", "-i", f"testsrc=s=160x90:r={rate}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=440:r=48000:d={seconds}",
        "-timecode", timecode, "-c:v", "{vcodec}", "-b:v", "500k", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", path,
    )
    return path


def make_recorder(path, after_midnight_s, seconds=4.0):
    """A sound file that says how long after midnight it started, as a recorder does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"sine=f=300:r=48000:d={seconds}",
               "-write_bext", "1", "-metadata", f"time_reference={int(after_midnight_s * 48000)}", path)
    return path


class Shoot(unittest.TestCase):
    """A made-up day: a camera with a clock, one without, and a recorder with a clock."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_FFMPEG:
            raise unittest.SkipTest("ffmpeg is not installed")
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.footage = cls.root / "day one"
        cls.quay = make_camera(cls.footage / "quay cam.mov", "01:00:00:00")
        cls.plain = make_video(cls.footage / "plain.mp4")
        cls.lapel = make_recorder(cls.footage / "lapel.wav", 3600.5)
        cls.settings = settings_in(cls.root / "home")


class StartTest(Shoot):
    def test_a_cameras_first_timecode_is_read(self):
        self.assertEqual(ffmpeg.start(self.quay), ffmpeg.Start("01:00:00:00", None))
        dropping = make_camera(self.root / "more" / "boats.mov", "00:59:59;28", seconds=1.0, rate="30000/1001")
        self.assertEqual(ffmpeg.start(dropping).timecode, "00:59:59;28")

    def test_a_recorders_time_after_midnight_is_read(self):
        self.assertEqual(ffmpeg.start(self.lapel), ffmpeg.Start(None, Fraction(7201, 2)))

    def test_a_file_with_no_clock_has_none(self):
        self.assertEqual(ffmpeg.start(self.plain), ffmpeg.Start())
        self.assertEqual(ffmpeg.start(make_still(self.root / "more" / "chart.png")), ffmpeg.Start())
        self.assertEqual(ffmpeg.start(self.root / "more" / "nothing.mov"), ffmpeg.Start())


class ExportTest(Shoot, DatabaseTest):
    def setUp(self):
        super().setUp()
        ingest.ingest(self.footage, "made-up-quay", settings=self.settings)
        self.assets = {Path(path).stem: asset_id for asset_id, path in self.query(
            "SELECT id, path FROM media.assets WHERE path LIKE %s", (f"{self.footage}/%",))}
        self.out = self.root / "exports" / self._testMethodName
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.settings.home),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def cut(self, name="made-up-quay-cut", **options):
        timelines.save(name, [
            Clip(self.assets["quay cam"], 0.2, 1.0, label="the tide"),
            over(self.assets["plain"], 0.0, 0.8, self.assets["lapel"], 1.0),
        ], project="made-up-quay", **options)
        return name

    def test_the_recordings_are_described_from_the_library_and_from_the_files(self):
        shelf = export.sources(timelines.load(self.cut()).clips)
        quay, plain, lapel = (shelf[str(self.assets[name])] for name in ("quay cam", "plain", "lapel"))
        self.assertEqual((quay.file, quay.medium, quay.fps, quay.has_audio, quay.width, quay.height,
                          quay.timecode, quay.after_midnight, quay.there),
                         (self.quay, "video", 25.0, True, 160, 90, "01:00:00:00", None, True))
        self.assertAlmostEqual(quay.duration_s, 3.0, delta=0.1)
        self.assertEqual((plain.file, plain.timecode, plain.after_midnight), (self.plain, None, None))
        self.assertEqual((lapel.file, lapel.medium, lapel.has_audio, lapel.timecode, lapel.after_midnight),
                         (self.lapel, "audio", True, None, Fraction(7201, 2)))

    def test_a_cut_is_written_as_two_files_and_a_page(self):
        made = export.export(self.cut(title="The quay"), self.out)
        self.assertEqual((made.folder, made.fcpxml, made.edl, made.readme),
                         (self.out, self.out / "made-up-quay-cut.fcpxml", self.out / "made-up-quay-cut.edl",
                          self.out / "README.md"))
        self.assertEqual((made.clips, made.sources, made.duration_s, made.rate, made.rates, made.mixed,
                          made.missing, made.left_out), (2, 3, 1.6, Fraction(25), (Fraction(25),), False, (), ()))
        self.assertEqual(sorted(file.name for file in self.out.iterdir()),
                         ["README.md", "made-up-quay-cut.edl", "made-up-quay-cut.fcpxml"])
        self.assertEqual(made.edl.read_text().splitlines(), [
            "TITLE: The quay",
            "FCM: NON-DROP FRAME",
            "",
            "001  AX       AA/V  C        01:00:00:05 01:00:01:00 00:00:00:00 00:00:00:20",
            "* FROM CLIP NAME: quay cam.mov",
            f"* SOURCE FILE: {self.quay}",
            "* COMMENT: the tide",
            "",
            "002  AX       V     C        00:00:00:00 00:00:00:20 00:00:00:20 00:00:01:15",
            "* FROM CLIP NAME: plain.mp4",
            f"* SOURCE FILE: {self.plain}",
            # The recorder started half a second past one, and the clip a second into it.
            "003  AX       A     C        01:00:01:13 01:00:02:08 00:00:00:20 00:00:01:15",
            "* FROM CLIP NAME: lapel.wav",
            f"* SOURCE FILE: {self.lapel}",
            "",
        ])
        root = ElementTree.fromstring(made.fcpxml.read_text())
        self.assertEqual(root.find("library/event/project").get("name"), "The quay")
        located = {asset.get("name"): asset.find("media-rep").get("src") for asset in root.iter("asset")}
        self.assertEqual(located["quay cam.mov"], "file://" + str(self.quay).replace(" ", "%20"))
        first, second = root.find("library/event/project/sequence/spine")
        self.assertEqual((first.get("name"), first.get("start"), first.get("duration")),
                         ("the tide", "18001/5s", "4/5s"))
        self.assertEqual((second[0].get("start"), second[0].get("offset")), ("7203/2s", "0s"))
        page = made.readme.read_text()
        self.assertIn("# The quay\n\n2 clips, 0:01.6, from 3 recordings, at 25 frames a second.", page)
        self.assertIn(f"\n- `{self.footage}`\n", page)
        self.assertIn("2 of the 3 carry a clock of their own", page)
        # The recordings were pointed at and not touched or copied.
        self.assertEqual(self.query("SELECT count(*) FROM media.assets WHERE path LIKE %s", (f"{self.out}/%",)),
                         [(0,)])

    def test_another_rate_can_be_asked_for(self):
        made = export.export(self.cut(), self.out, fps=29.97)
        self.assertEqual((made.rate, made.clips), (Fraction(30000, 1001), 2))
        self.assertIn("00:00:00:00 00:00:00:24", made.edl.read_text())

    def test_what_cannot_be_exported_is_refused_by_name(self):
        with self.assertRaisesRegex(EditError, "no timeline is called 'made-up-nothing'"):
            export.export("made-up-nothing", self.out)
        timelines.save("made-up-heard", [Clip(self.assets["lapel"], 0.0, 1.0)])
        with self.assertRaisesRegex(EditError, "clip 1 of 'made-up-heard' takes its picture from lapel.wav, which"):
            export.export("made-up-heard", self.out)
        timelines.save("made-up-blink", [Clip(self.assets["plain"], 0.0, 0.005)])
        with self.assertRaisesRegex(EditError, "'made-up-blink' is shorter than one frame"):
            export.export("made-up-blink", self.out)
        self.assertFalse(self.out.exists())

    def test_me_export_says_what_it_wrote(self):
        name = self.cut()
        status, out, err = test_cli.run("export", name, "--to", str(self.out))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            "made-up-quay-cut: 2 clips, 0:01.6, at 25 frames a second, from 3 recordings",
            str(self.out / "made-up-quay-cut.fcpxml"), str(self.out / "made-up-quay-cut.edl"),
            str(self.out / "README.md"),
        ])
        status, out, err = test_cli.run("export", name)
        self.assertEqual((status, err), (0, ""))
        self.assertTrue((self.settings.work_dir / "exports" / name / f"{name}.fcpxml").is_file())

    def test_me_export_says_when_a_recording_is_not_there(self):
        name = self.cut()
        self.query("UPDATE media.assets SET path = '/made-up/gone.mp4', fps = 29.97 WHERE id = %s RETURNING id",
                   (self.assets["plain"],))
        timelines.save("made-up-short", [Clip(self.assets["quay cam"], 0.0, 0.01), Clip(self.assets["plain"], 0, 1)])
        status, out, err = test_cli.run("export", name, "--to", str(self.out))
        self.assertEqual((status, err), (1, "me: 1 of the recordings it points at are not there: gone.mp4\n"))
        self.assertIn("  shot at 25, 29.97 frames a second: check the last clip against its sound\n", out)
        self.assertIn("**1 of them were not there when this was written,**", (self.out / "README.md").read_text())
        status, out, _ = test_cli.run("export", "made-up-short", "--to", str(self.out / "short"))
        self.assertEqual(status, 1)
        self.assertIn("  too short for a frame, and left out: clip 1\n", out)
        self.assertEqual(test_cli.run("export", "made-up-nothing"),
                         (1, "", "me: no timeline is called 'made-up-nothing'; `me timelines` lists them\n"))
