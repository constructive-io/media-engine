"""Cuts made into files, on media made on the spot, and the files looked at."""

import math
import os
import struct
import tempfile
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from unittest import mock

from constructive_media import derive, ffmpeg, ingest
from constructive_media.edit import EditError, render, timelines
from constructive_media.edit.render import Batch, Loudness, Plan, Recording, Stretch
from constructive_media.edit.timelines import Clip

from . import test_cli, test_cut
from .support import HAVE_FFMPEG, TEST_DATABASE_VARIABLE, DatabaseTest, make_still, make_video, settings_in

FILM = Fraction(24000, 1001)
VIDEO = Fraction(30000, 1001)
LISTENED_AT = 8000


def recording(name, medium="video", *, fps=25.0, sound=True, seconds=60.0):
    return Recording(name, Path(f"/made-up/{name}"), medium, seconds, fps if medium == "video" else None, sound)


#: A camera, another camera, a microphone, a camera that recorded no sound, and a photograph.
SHELF = {each.asset_id: each for each in (
    recording("quay.mov"), recording("boats.mov", fps=29.97), recording("lapel.wav", "audio"),
    recording("mute.mov", sound=False), recording("chart.png", "image"),
)}


def planned(clips, per_s=Fraction(25), **options):
    return render.plan(clips, SHELF, width=960, height=540, frames_per_s=per_s, **options)


def over(picture, start, end, sound, sound_start):
    """A clip seen through one recording and heard through another."""
    return Clip(picture, start, end, sound=sound, sound_start_s=sound_start, sound_end_s=sound_start + end - start)


class SizesTest(unittest.TestCase):
    def test_a_canvas_has_a_size_at_any_height(self):
        self.assertEqual(render.size("landscape"), (960, 540))
        self.assertEqual(render.size("vertical"), (608, 1080))
        self.assertEqual(render.size("square"), (720, 720))
        self.assertEqual(render.size("landscape", 1080), (1920, 1080))
        self.assertEqual(render.size("vertical", 1920), (1080, 1920))
        # Encoders take even numbers only.
        self.assertEqual(render.size("landscape", 271), (484, 272))
        with self.assertRaisesRegex(EditError, "no canvas is called 'round'"):
            render.size("round")
        with self.assertRaisesRegex(EditError, "too small to see"):
            render.size("square", 4)

    def test_a_frame_rate_is_the_fraction_a_camera_means(self):
        self.assertEqual(render.rate(23.976), FILM)
        self.assertEqual(render.rate(29.97), VIDEO)
        self.assertEqual(render.rate(29.97003), VIDEO)
        self.assertEqual(render.rate(59.94), Fraction(60000, 1001))
        self.assertEqual(render.rate(25.0), 25)
        self.assertEqual(render.rate(24), 24)
        self.assertEqual(render.rate(12.5), Fraction(25, 2))
        self.assertEqual(render.rate(None), 30)
        self.assertEqual(render.rate(0), 30)

    def test_a_cut_runs_at_the_rate_most_of_it_was_shot_at(self):
        mostly_boats = [Clip("quay.mov", 0.0, 4.0), Clip("boats.mov", 0.0, 5.0), Clip("chart.png", 0.0, 9.0)]
        self.assertEqual(render.usual(mostly_boats, SHELF), VIDEO)
        self.assertEqual(render.usual(mostly_boats[:1] * 2 + mostly_boats[1:], SHELF), 25)
        # A clip lasts as long as its sound, and that is the time that counts.
        self.assertEqual(render.usual([over("quay.mov", 0.0, 6.0, "lapel.wav", 0.0), Clip("boats.mov", 0.0, 5.0)],
                                      SHELF), 25)
        self.assertEqual(render.usual([Clip("chart.png", 0.0, 3.0)], SHELF), 30)

    def test_a_picture_is_given_the_bits_its_size_is_worth_or_the_bits_that_fit(self):
        self.assertEqual(render.bitrate(960, 540, Fraction(30)), 2_954_880)
        self.assertEqual(render.bitrate(16, 16, Fraction(30)), render.LEAST_BITRATE)
        # Ten megabytes over a hundred seconds, less the sound.
        self.assertEqual(render.bitrate(960, 540, Fraction(30), megabytes=10, duration_s=100.0), 776_000 - 160_000)
        self.assertEqual(render.bitrate(960, 540, Fraction(30), megabytes=10, duration_s=100.0, silent=True), 776_000)
        self.assertEqual(render.bitrate(960, 540, Fraction(30), megabytes=1, duration_s=600.0), render.LEAST_BITRATE)
        with self.assertRaisesRegex(EditError, "some megabytes"):
            render.bitrate(960, 540, Fraction(30), megabytes=0, duration_s=100.0)


class LoudnessTest(unittest.TestCase):
    def test_gain_is_the_distance_to_the_target(self):
        self.assertEqual(render.gain(Loudness(-23.0)), 7.0)
        self.assertEqual(render.gain(Loudness(-12.5)), -3.5)
        self.assertEqual(render.gain(Loudness(-23.0), target=-14.0), 9.0)

    def test_a_room_is_not_turned_up_into_a_voice(self):
        self.assertEqual(render.gain(Loudness(-48.0)), 20.0)
        self.assertEqual(render.gain(Loudness(-48.0), most=12.0), 12.0)

    def test_silence_is_left_alone(self):
        self.assertEqual(render.gain(Loudness(-70.5)), 0.0)
        self.assertEqual(render.gain(Loudness(None)), 0.0)
        self.assertEqual(render.gain(None), 0.0)

    def test_the_measurement_is_read_from_what_ffmpeg_prints(self):
        printed = ('[Parsed_loudnorm_0 @ 0x1] \n{\n\t"input_i" : "-27.61",\n\t"input_tp" : "-4.47",\n'
                   '\t"input_lra" : "18.06",\n\t"output_i" : "-23.5"\n}\n[out#0/null @ 0x2] video:0KiB\n')
        self.assertEqual(render.heard(printed), Loudness(-27.61, -4.47))
        # Too short to measure, or nothing in it.
        self.assertEqual(render.heard('{\n\t"input_i" : "-inf",\n\t"input_tp" : "-inf"\n}'), Loudness(None, None))
        self.assertEqual(render.heard("nothing was measured"), Loudness(None, None))
        self.assertEqual(render.heard("{ not what was expected }"), Loudness(None, None))


class PlanTest(unittest.TestCase):
    def test_frames_are_counted_from_the_start_of_the_cut(self):
        # Forty clips of a length no frame rate divides: each rounds, and none of it adds up.
        made = planned([Clip("boats.mov", 0.37 * n, 0.37 * (n + 1)) for n in range(40)], VIDEO)
        self.assertAlmostEqual(made.duration_s, 14.8)
        self.assertEqual(made.frames, round(14.8 * VIDEO))
        self.assertEqual({piece.frames for piece in made.pieces}, {11, 12})
        shown = 0
        for piece, starts in zip(made.pieces, made.times):
            self.assertLessEqual(abs(shown / VIDEO - starts), 0.5 / VIDEO)
            shown += piece.frames

    def test_a_clip_shorter_than_a_frame_can_have_none_and_time_is_still_kept(self):
        made = planned([Clip("quay.mov", 0.0, 0.015), Clip("quay.mov", 5.0, 5.015), Clip("quay.mov", 9.0, 10.0)])
        self.assertEqual([piece.frames for piece in made.pieces], [0, 1, 25])
        self.assertEqual(sum(stretch.samples for stretch in made.stretches), 49440)

    def test_sound_that_carries_on_is_one_stretch(self):
        # A cutaway: the talk goes on under other footage and comes back.
        made = planned([Clip("quay.mov", 10.0, 14.0), over("boats.mov", 3.0, 5.5, "quay.mov", 14.0),
                        Clip("quay.mov", 16.5, 20.0)])
        self.assertEqual(made.stretches, (Stretch(Path("/made-up/quay.mov"), 10.0, 480000, 0, 3),))
        self.assertEqual([piece.file.name for piece in made.pieces], ["quay.mov", "boats.mov", "quay.mov"])
        self.assertEqual([piece.start_s for piece in made.pieces], [10.0, 3.0, 16.5])

    def test_sound_that_jumps_is_two(self):
        made = planned([Clip("quay.mov", 10.0, 14.0), Clip("quay.mov", 14.5, 16.0), Clip("quay.mov", 16.0, 17.0),
                        Clip("boats.mov", 17.0, 18.0)])
        self.assertEqual([(each.file.name, each.start_s, each.first, each.count, each.duration_s)
                          for each in made.stretches],
                         [("quay.mov", 10.0, 0, 1, 4.0), ("quay.mov", 14.5, 1, 2, 2.5), ("boats.mov", 17.0, 3, 1, 1.0)])

    def test_a_microphone_is_heard_under_whatever_is_seen(self):
        made = planned([over("quay.mov", 10.0, 14.0, "lapel.wav", 2.0), over("boats.mov", 0.0, 3.0, "lapel.wav", 6.0)])
        self.assertEqual(made.stretches, (Stretch(Path("/made-up/lapel.wav"), 2.0, 336000, 0, 2),))

    def test_what_has_no_sound_is_heard_as_silence(self):
        made = planned([Clip("mute.mov", 0.0, 2.0), Clip("chart.png", 0.0, 1.0), Clip("quay.mov", 0.0, 1.0)])
        self.assertEqual([(each.file, each.first, each.count) for each in made.stretches],
                         [(None, 0, 2), (Path("/made-up/quay.mov"), 2, 1)])
        self.assertEqual([piece.still for piece in made.pieces], [False, True, False])

    def test_a_silent_plan_has_no_sound_at_all(self):
        made = planned([Clip("quay.mov", 0.0, 2.0), Clip("boats.mov", 0.0, 2.0)], silent=True)
        self.assertEqual(made.stretches, ())
        self.assertEqual(made.frames, 100)

    def test_people_are_remembered_for_the_clips_that_have_them(self):
        made = planned([Clip("quay.mov", 0.0, 2.0), Clip("boats.mov", 0.0, 2.0)], fit="crop", centres={1: (0.3, 0.4)})
        self.assertEqual([piece.centre for piece in made.pieces], [(0.5, 0.5), (0.3, 0.4)])
        with self.assertRaisesRegex(EditError, "fitted by one of: pad, crop; not 'stretch'"):
            planned([Clip("quay.mov", 0.0, 2.0)], fit="stretch")


class BatchesTest(unittest.TestCase):
    def test_a_short_cut_is_one_batch(self):
        made = planned([Clip("quay.mov", 0.0, 2.0), Clip("boats.mov", 0.0, 2.0)])
        self.assertEqual(render.batches(made), [Batch(made.pieces, made.stretches)])

    def test_stretches_of_sound_are_kept_whole_where_they_fit(self):
        # Five stretches of three clips each: four fit in twelve, the fifth starts the next.
        clips = [Clip("quay.mov", 100.0 * run + clip, 100.0 * run + clip + 1.0) for run in range(5) for clip in range(3)]
        made = planned(clips)
        self.assertEqual([each.count for each in made.stretches], [3] * 5)
        first, second = render.batches(made)
        self.assertEqual((len(first.pieces), len(second.pieces)), (12, 3))
        self.assertEqual(first.stretches, made.stretches[:4])
        self.assertEqual(second.stretches, made.stretches[4:])
        # Smaller batches break between stretches before they break inside one.
        self.assertEqual([len(batch.pieces) for batch in render.batches(made, most=5)], [3, 3, 3, 3, 3])

    def test_a_stretch_too_long_for_a_batch_is_divided_to_the_sample(self):
        made = planned([Clip("boats.mov", 0.37 * n, 0.37 * (n + 1)) for n in range(40)], VIDEO)
        (whole,) = made.stretches
        parts = render.batches(made)
        self.assertEqual([len(batch.pieces) for batch in parts], [12, 12, 12, 4])
        halves = [batch.stretches[0] for batch in parts]
        self.assertEqual(sum(half.samples for half in halves), whole.samples)
        self.assertEqual([(half.opens, half.closes) for half in halves],
                         [(True, False), (False, False), (False, False), (False, True)])
        for half, batch in zip(halves, range(4)):
            self.assertAlmostEqual(half.start_s, 0.37 * 12 * batch)
            self.assertEqual((half.first, half.count), (12 * batch, len(parts[batch].pieces)))

    def test_a_silent_cut_is_divided_by_clips(self):
        made = planned([Clip("quay.mov", float(n), n + 1.0) for n in range(30)], silent=True)
        self.assertEqual([(len(batch.pieces), batch.stretches) for batch in render.batches(made)],
                         [(12, ()), (12, ()), (6, ())])

    def test_gain_goes_with_each_part(self):
        made = planned([Clip("quay.mov", float(n), n + 1.0) for n in range(14)])
        (whole,) = made.stretches
        loud = Plan(made.width, made.height, made.rate, made.fit, made.pieces,
                    (Stretch(whole.file, whole.start_s, whole.samples, whole.first, whole.count, 6.5),), made.times)
        self.assertEqual([batch.stretches[0].gain_db for batch in render.batches(loud)], [6.5, 6.5])


class GraphTest(unittest.TestCase):
    def graph(self, clips, per_s=Fraction(25), most=12, **options):
        made = planned(clips, per_s, **options)
        return [render.graph(made, batch) for batch in render.batches(made, most)]

    def test_each_clip_is_read_from_its_start_for_its_frames(self):
        ((inputs, filters),) = self.graph([Clip("quay.mov", 10.0, 12.0), over("boats.mov", 3.0, 4.0, "quay.mov", 12.0)],
                                          VIDEO)
        self.assertEqual(inputs, [
            "-an", "-ss", "10.000000", "-t", "2.068733", "-i", "/made-up/quay.mov",
            "-an", "-ss", "3.000000", "-t", "1.067733", "-i", "/made-up/boats.mov",
            "-vn", "-ss", "10.000000", "-t", "3.100000", "-i", "/made-up/quay.mov",
        ])
        first, second, joined, sound, heard = filters.split(";")
        self.assertTrue(first.startswith(
            "[0:v:0]fps=30000/1001:start_time=0,tpad=stop=-1:stop_mode=clone,trim=end_frame=60,scale=960:540:"))
        self.assertTrue(first.endswith(",format=yuv420p,setpts=PTS-STARTPTS[v0]"))
        self.assertIn("trim=end_frame=30,", second)
        self.assertEqual(joined, "[v0][v1]concat=n=2:v=1:a=0[v]")
        self.assertEqual(sound, "[2:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                                "highpass=f=80,afade=t=in:st=0:d=0.02,afade=t=out:st=2.980000:d=0.02,"
                                "apad=whole_len=144000,atrim=end_sample=144000,asetpts=PTS-STARTPTS[a0]")
        self.assertEqual(heard, "[a0]concat=n=1:v=0:a=1[a]")

    def test_a_picture_is_padded_or_it_fills_the_canvas(self):
        ((_, padded),) = self.graph([Clip("quay.mov", 0.0, 1.0)], silent=True)
        self.assertIn("scale=960:540:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                      "pad=960:540:(ow-iw)/2:(oh-ih)/2:black,setsar=1", padded)
        self.assertNotIn("[a]", padded)
        ((_, cropped),) = self.graph([Clip("quay.mov", 0.0, 1.0)], silent=True, fit="crop", centres={0: (0.25, 0.6)})
        self.assertIn("crop='min(iw,ih*960/540)':'min(ih,iw*540/960)':'clip(iw*0.2500-ow/2,0,iw-ow)':"
                      "'clip(ih*0.6000-oh/2,0,ih-oh)',scale=960:540,setsar=1", cropped)

    def test_sound_fades_only_where_it_starts_and_stops(self):
        graphs = self.graph([Clip("quay.mov", float(n), n + 1.0) for n in range(5)], most=2)
        fades = [[step.split(":")[0] for step in filters.split(";")[-2].split(",") if step.startswith("afade")]
                 for _, filters in graphs]
        self.assertEqual(fades, [["afade=t=in"], [], ["afade=t=out"]])

    def test_gain_is_one_number_for_the_whole_stretch(self):
        made = planned([Clip("quay.mov", 0.0, 2.0)])
        (whole,) = made.stretches
        loud = Plan(made.width, made.height, made.rate, made.fit, made.pieces,
                    (Stretch(whole.file, whole.start_s, whole.samples, whole.first, whole.count, -3.25),), made.times)
        _, filters = render.graph(loud, render.batches(loud)[0], highpass_hz=0, fade_s=0)
        self.assertIn("channel_layouts=stereo,volume=-3.25dB,apad=whole_len=96000", filters)

    def test_a_photograph_is_held_and_silence_is_made(self):
        ((inputs, filters),) = self.graph([Clip("chart.png", 0.0, 2.0)])
        self.assertEqual(inputs, [
            "-loop", "1", "-framerate", "25/1", "-t", "2.080000", "-i", "/made-up/chart.png",
            "-f", "lavfi", "-t", "2.100000", "-i", "anullsrc=r=48000:cl=stereo",
        ])
        self.assertIn("[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_len=96000",
                      filters)

    def test_a_clip_with_no_frames_is_not_read(self):
        ((inputs, filters),) = self.graph([Clip("quay.mov", 0.0, 0.01), Clip("quay.mov", 5.0, 6.0)], silent=True)
        self.assertEqual(inputs.count("-i"), 1)
        self.assertIn("[0:v:0]", filters)
        self.assertTrue(filters.endswith("[v0]concat=n=1:v=1:a=0[v]"))


def levels(path, window_s=0.02):
    """How loud a file is, in decibels, through each short window of it, and its highest peak."""
    heard = b"".join(ffmpeg.samples(path, rate=LISTENED_AT))
    values = struct.unpack(f"<{len(heard) // 2}h", heard)
    size = int(LISTENED_AT * window_s)
    found = []
    for start in range(0, len(values) - size + 1, size):
        power = sum(value * value for value in values[start:start + size]) / size
        found.append(20 * math.log10(max(math.sqrt(power), 1.0) / 32768))
    return found, max(map(abs, values)) / 32768


def onset(path, above=0.01, rate=48000):
    """When a file first makes a sound, in seconds."""
    heard = b"".join(ffmpeg.samples(path, rate=rate))
    values = struct.unpack(f"<{len(heard) // 2}h", heard)
    return next(index for index, value in enumerate(values) if abs(value) > above * 32768) / rate


def colour(path, at_s, across=0.5, down=0.5):
    """The colour of one point of the frame at a moment: red, green and blue, each 0 to 255."""
    described = ffmpeg.probe(path)
    pixels = ffmpeg.frame(path, at_s)
    where = (int(described.height * down) * described.width + int(described.width * across)) * 3
    return tuple(pixels[where:where + 3])


def is_red(found):
    return found[0] > 150 and found[1] < 90 and found[2] < 90


def is_dark_green(found):
    return max(found) < 110 and found[1] > found[0] + 15 and found[1] > found[2] + 15


def is_pale(found):
    return min(found) > 150


def is_black(found):
    return max(found) < 40


def is_blue(found):
    return found[2] > 150 and found[0] < 90 and found[1] < 90


def make_microphone(path, seconds=4.0, rate=48000):
    """Silence for a second, then a hum with one loud knock in it at two seconds."""
    values = []
    for index in range(int(seconds * rate)):
        at = index / rate
        loud = 0.0 if at < 1.0 else 0.9 if 2.0 <= at < 2.01 else 0.1
        values.append(int(32767 * loud * math.sin(2 * math.pi * 300 * at)))
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(struct.pack(f"<{len(values)}h", *values))
    return path


def make_halves(path, seconds=1.0):
    """A video red on its left half and blue on its right, with no sound."""
    ffmpeg.encode(
        "-f", "lavfi", "-i", f"color=c=red:s=80x90:r=25:d={seconds}",
        "-f", "lavfi", "-i", f"color=c=blue:s=80x90:r=25:d={seconds}",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]", "-map", "[v]",
        "-c:v", "{vcodec}", "-b:v", "1M", "-pix_fmt", "yuv420p", path,
    )
    return path


class Media(unittest.TestCase):
    """Made-up recordings: two cameras of different shapes, a microphone, and a photograph."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_FFMPEG:
            raise unittest.SkipTest("ffmpeg is not installed")
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.footage = cls.root / "footage"
        # Bright red, then dark green, then pale blue, a second each, with a steady tone.
        cls.wide = make_video(cls.footage / "wide.mp4")
        cls.tall = make_video(cls.footage / "tall.mp4", colours=("white", "white"), size="90x160", rate=30)
        cls.halves = make_halves(cls.footage / "halves.mp4")
        cls.lapel = make_microphone(cls.footage / "lapel.wav")
        cls.chart = make_still(cls.footage / "chart.png")
        cls.settings = settings_in(cls.root / "home")


class BuildTest(Media):
    def setUp(self):
        self.shelf = {
            "wide": Recording("wide", self.wide, "video", 3.0, 25.0),
            "tall": Recording("tall", self.tall, "video", 2.0, 30.0),
            "halves": Recording("halves", self.halves, "video", 1.0, 25.0, has_audio=False),
            "lapel": Recording("lapel", self.lapel, "audio", 4.0),
            "chart": Recording("chart", self.chart, "image"),
        }

    def plan(self, clips, canvas="landscape", height=180, **options):
        width, tall = render.size(canvas, height)
        return render.plan(clips, self.shelf, width=width, height=tall,
                           frames_per_s=render.usual(clips, self.shelf), **options)

    def build(self, made, name="out.mp4", **options):
        out = self.root / self._testMethodName / name
        encoder = render.build(made, out, self.root / "work", **options)
        self.assertIn(encoder, ffmpeg.h264_candidates())
        self.assertEqual(list((self.root / "work").iterdir()), [], "nothing is left behind in the work folder")
        return out

    def test_a_cutaway_changes_the_picture_and_leaves_the_sound_alone(self):
        made = self.plan([Clip("wide", 0.2, 1.0), over("tall", 0.0, 0.8, "wide", 1.0), Clip("wide", 1.8, 2.6)])
        self.assertEqual([(each.first, each.count) for each in made.stretches], [(0, 3)])
        out = self.build(made)
        described = ffmpeg.probe(out)
        self.assertEqual((described.width, described.height, described.fps), (320, 180, 25.0))
        self.assertAlmostEqual(described.duration_s, 2.4, delta=0.07)
        self.assertEqual(set(ffmpeg.frame_sizes(out, [0.1, 0.9, 1.3, 2.3])), {(320, 180)})
        self.assertTrue(is_red(colour(out, 0.4)))
        self.assertTrue(is_pale(colour(out, 1.2)))
        self.assertTrue(is_pale(colour(out, 2.0)))
        # The tone runs through both joins without a dip.
        heard, _ = levels(out)
        body = heard[3:-5]
        self.assertGreater(len(body), 100)
        self.assertLess(max(body) - min(body), 1.0)

    def test_sound_that_jumps_is_faded_out_and_in(self):
        # The same measurement, where there is a join to hear.
        made = self.plan([Clip("wide", 0.2, 1.0), Clip("wide", 1.2, 2.0)])
        self.assertEqual(len(made.stretches), 2)
        heard, _ = levels(self.build(made))
        body = heard[3:-5]
        self.assertGreater(max(body) - min(body), 3.0)
        self.assertEqual(body.index(min(body)) + 3, 39)  # the window that ends at the join, 0.8 seconds in

    def test_picture_and_sound_start_together(self):
        # The picture turns green one second into the camera's recording, and the
        # microphone starts to hum one second into its own. Both are half a second into the cut.
        made = self.plan([over("wide", 0.5, 2.5, "lapel", 0.5)])
        out = self.build(render.levelled(made))
        self.assertAlmostEqual(onset(out), 0.5, delta=0.003)
        self.assertTrue(is_red(colour(out, 0.42)))
        self.assertTrue(is_dark_green(colour(out, 0.54)))

    def test_peaks_are_held_under_the_ceiling(self):
        made = render.levelled(self.plan([over("wide", 0.0, 3.0, "lapel", 1.0)]))
        (stretch,) = made.stretches
        # Turned up this far, the knock would be well over full scale.
        self.assertGreater(0.9 * 10 ** (stretch.gain_db / 20), 1.25)
        _, peak = levels(self.build(made))
        # The ceiling is on the sound before it is packed; packing moves a peak a little.
        self.assertLess(20 * math.log10(peak), -0.5)
        self.assertGreater(20 * math.log10(peak), -4.0)
        _, lower = levels(self.build(made, "lower.mp4", ceiling_db=-9.0))
        self.assertLess(20 * math.log10(lower), -8.0)

    def test_sound_is_brought_to_the_loudness_asked_for(self):
        clips = [Clip("wide", 0.0, 3.0)]
        before = render.loudness(self.wide, 0.0, 3.0).integrated
        for target in (-16.0, -27.0):
            made = render.levelled(self.plan(clips), target)
            self.assertAlmostEqual(made.stretches[0].gain_db, target - before, delta=0.6)
            out = self.build(made, f"at{target:.0f}.mp4")
            self.assertAlmostEqual(render.loudness(out, 0.0, 3.0).integrated, target, delta=1.0)
        # Left alone, it comes out as loud as it went in.
        self.assertAlmostEqual(render.loudness(self.build(self.plan(clips), "alone.mp4"), 0.0, 3.0).integrated,
                               before, delta=1.0)

    def test_a_long_cut_is_made_in_parts_and_stays_in_step(self):
        made = self.plan([Clip("wide", 0.08 * n, 0.08 * (n + 1)) for n in range(30)])
        self.assertEqual(len(render.batches(made)), 3)
        self.assertEqual((made.frames, len(made.stretches)), (60, 1))
        out = self.build(made)
        self.assertAlmostEqual(ffmpeg.probe(out).duration_s, 2.4, delta=0.07)
        self.assertTrue(is_red(colour(out, 0.9)))
        self.assertTrue(is_dark_green(colour(out, 1.1)))
        self.assertTrue(is_pale(colour(out, 2.1)))
        heard, _ = levels(out)
        body = heard[3:-5]
        self.assertLess(max(body) - min(body), 1.0)

    def test_a_picture_of_another_shape_is_shown_whole_between_bars(self):
        out = self.build(self.plan([Clip("tall", 0.0, 1.0)]))
        self.assertTrue(is_pale(colour(out, 0.5)))
        self.assertTrue(is_black(colour(out, 0.5, across=0.1)))
        self.assertTrue(is_black(colour(out, 0.5, across=0.9)))

    def test_or_it_fills_the_canvas(self):
        out = self.build(self.plan([Clip("tall", 0.0, 1.0)], fit="crop"))
        self.assertEqual(set(ffmpeg.frame_sizes(out, [0.5])), {(320, 180)})
        for across in (0.1, 0.5, 0.9):
            self.assertTrue(is_pale(colour(out, 0.5, across=across)))

    def test_a_crop_keeps_the_people_in_it(self):
        clips = [Clip("halves", 0.0, 1.0)]
        middle = self.build(self.plan(clips, "square", fit="crop"), "middle.mp4")
        self.assertEqual(set(ffmpeg.frame_sizes(middle, [0.5])), {(180, 180)})
        self.assertTrue(is_red(colour(middle, 0.5, across=0.2)))
        self.assertTrue(is_blue(colour(middle, 0.5, across=0.8)))
        left = self.build(self.plan(clips, "square", fit="crop", centres={0: (0.2, 0.5)}), "left.mp4")
        self.assertTrue(is_red(colour(left, 0.5, across=0.2)))
        self.assertTrue(is_red(colour(left, 0.5, across=0.8)))
        right = self.build(self.plan(clips, "square", fit="crop", centres={0: (0.95, 0.5)}), "right.mp4")
        self.assertTrue(is_blue(colour(right, 0.5, across=0.2)))

    def test_what_recorded_no_sound_is_silent_and_a_silent_render_has_none(self):
        clips = [Clip("halves", 0.0, 1.0), Clip("chart", 0.0, 0.5)]
        quiet = self.build(self.plan(clips))
        self.assertTrue(ffmpeg.probe(quiet).has_audio)
        self.assertAlmostEqual(ffmpeg.probe(quiet).duration_s, 1.5, delta=0.07)
        self.assertLess(max(levels(quiet)[0]), -80.0)
        self.assertFalse(is_black(colour(quiet, 1.25)))
        silent = self.build(self.plan(clips, silent=True), "silent.mp4")
        self.assertFalse(ffmpeg.probe(silent).has_audio)
        self.assertAlmostEqual(ffmpeg.probe(silent).duration_s, 1.5, delta=0.05)

    def test_a_recording_that_stops_short_holds_its_last_frame(self):
        # The camera stopped at three seconds. The clip asks for half a second more.
        out = self.build(self.plan([Clip("wide", 2.5, 3.5)]))
        self.assertAlmostEqual(ffmpeg.probe(out).duration_s, 1.0, delta=0.07)
        self.assertTrue(is_pale(colour(out, 0.9)))
        heard, _ = levels(out)
        self.assertGreater(heard[10], -40.0)
        self.assertLess(heard[40], -80.0)

    def test_two_moments_are_put_side_by_side(self):
        out = render.side_by_side((self.wide, 0.2), (self.wide, 1.2), self.root / "pairs" / "one.mp4",
                                  seconds=0.6, height=90)
        described = ffmpeg.probe(out)
        self.assertEqual((described.width, described.height, described.has_audio), (326, 90, False))
        self.assertAlmostEqual(described.duration_s, 0.6, delta=0.07)
        self.assertTrue(is_red(colour(out, 0.3, across=0.25)))
        self.assertTrue(is_pale(colour(out, 0.3, across=0.497)))
        self.assertTrue(is_dark_green(colour(out, 0.3, across=0.75)))


class Library(Media, DatabaseTest):
    """The made-up recordings, ingested, and a cut of them."""

    def setUp(self):
        super().setUp()
        ingest.ingest(self.footage, "made-up-quay", settings=self.settings)
        self.assets = {Path(path).stem: asset_id for asset_id, path in self.query(
            "SELECT id, path FROM media.assets WHERE path LIKE %s", (f"{self.footage}/%",))}
        self.out = self.root / "renders" / f"{self._testMethodName}.mp4"

    def clip(self, name, start, end, sound=None, sound_start=None):
        if sound is None:
            return Clip(self.assets[name], start, end)
        return over(self.assets[name], start, end, self.assets[sound], sound_start)

    def quay(self, name="made-up-quay-cut", **options):
        timelines.save(name, [self.clip("wide", 0.2, 1.0), self.clip("tall", 0.0, 0.8, "wide", 1.0),
                              self.clip("wide", 1.8, 2.6)], project="made-up-quay", **options)
        return name

    def render(self, name, **options):
        return render.render(name, self.out, settings=self.settings, **options)


class RenderTest(Library):
    def test_a_cut_is_rendered_and_what_was_made_is_recorded(self):
        made = self.render(self.quay(), height=180)
        self.assertEqual((made.path, made.clips, made.width, made.height, made.rate),
                         (self.out, 3, 320, 180, Fraction(25)))
        self.assertAlmostEqual(made.duration_s, 2.4)
        self.assertLess(abs(made.adrift_s), 0.07)
        self.assertEqual((made.sizes, made.looked, made.uniform), (((320, 180),), 6, True))
        self.assertEqual(len(made.gains), 1)
        self.assertGreater(made.gains[0], 1.0)
        self.assertIn(made.encoder, ffmpeg.h264_candidates())
        self.assertTrue(is_red(colour(self.out, 0.4)))
        self.assertTrue(is_pale(colour(self.out, 1.2)))
        self.assertEqual(self.query(
            "SELECT timeline, width, height, uniform, samples, canvas FROM media_video.render_geometry WHERE path = %s",
            (str(self.out),)), [("made-up-quay-cut", 320, 180, True, 6, "landscape")])
        made_from = derive.lineage(self.out)
        self.assertEqual((made_from.kind, made_from.tool), ("render", "media render"))
        self.assertEqual(made_from.params, {
            "timeline": "made-up-quay-cut", "revision": 1, "canvas": "landscape", "fit": "pad", "width": 320,
            "height": 180, "fps": "25", "silent": False, "originals": False, "lufs": -16.0,
        })
        used = self.query(
            """SELECT a.path, s.role, s.start_s, s.end_s
                 FROM media.derivation_sources s
                 JOIN media.derivations d ON d.id = s.derivation_id
                 JOIN media.assets made ON made.id = d.output_id
                 JOIN media.assets a ON a.id = s.source_id
                WHERE made.path = %s ORDER BY a.path, s.role""", (str(self.out),))
        self.assertEqual([(Path(path).name, role, round(start, 3), round(end, 3)) for path, role, start, end in used],
                         [("tall.mp4", "picture", 0.0, 0.8), ("wide.mp4", "picture", 0.2, 2.6),
                          ("wide.mp4", "sound", 0.2, 2.6)])
        self.assertEqual(self.query("SELECT medium, project, width, has_audio FROM media.assets WHERE path = %s",
                                    (str(self.out),)), [("video", "made-up-quay", 320, True)])

    def test_the_small_copies_are_read_unless_the_recordings_are_asked_for(self):
        clips = timelines.load(self.quay()).clips
        small = render.recordings(clips)
        wide = small[str(self.assets["wide"])]
        self.assertIn(self.settings.work_dir / "proxies", wide.file.parents)
        self.assertEqual((wide.medium, wide.duration_s, wide.fps, wide.has_audio), ("video", 3.0, 25.0, True))
        self.assertEqual(render.recordings(clips, originals=True)[str(self.assets["wide"])].file, self.wide)
        # A microphone has no small copy: the recording is what is read.
        self.assertEqual(render.recordings([self.clip("wide", 0.0, 1.0, "lapel", 1.0)])[str(self.assets["lapel"])],
                         Recording(str(self.assets["lapel"]), self.lapel, "audio", 4.0, None, True))
        self.assertEqual(self.render("made-up-quay-cut", height=180, originals=True).sizes, ((320, 180),))
        self.assertTrue(derive.lineage(self.out).params["originals"])

    def test_a_cut_is_rendered_to_its_own_canvas_unless_another_is_given(self):
        name = self.quay(canvas="vertical", fit="crop")
        made = self.render(name, height=160, silent=True)
        self.assertEqual(((made.width, made.height), made.sizes, made.gains), ((90, 160), ((90, 160),), ()))
        self.assertFalse(ffmpeg.probe(self.out).has_audio)
        # Filled, not padded: the top of the frame is picture.
        self.assertTrue(is_red(colour(self.out, 0.4, down=0.05)))
        self.assertEqual(derive.lineage(self.out).params["fit"], "crop")
        self.assertEqual([(Path(path).name, role) for path, role in self.query(
            """SELECT a.path, s.role FROM media.derivation_sources s
                 JOIN media.derivations d ON d.id = s.derivation_id
                 JOIN media.assets made ON made.id = d.output_id
                 JOIN media.assets a ON a.id = s.source_id
                WHERE made.path = %s ORDER BY a.path""", (str(self.out),))],
            [("tall.mp4", "picture"), ("wide.mp4", "picture")])
        padded = self.render(name, height=160, silent=True, fit="pad", canvas="square", fps=50)
        self.assertEqual((padded.sizes, padded.rate), (((160, 160),), Fraction(50)))
        self.assertTrue(is_black(colour(self.out, 0.4, down=0.05)))
        self.assertEqual(self.query(
            "SELECT width, height, canvas FROM media_video.render_geometry WHERE path = %s ORDER BY width",
            (str(self.out),)), [(90, 160, "vertical"), (160, 160, "square")])

    def people(self, name, *found):
        (shot,) = self.query(
            "INSERT INTO media_video.shots (asset_id, idx, start_s, end_s) VALUES (%s, 0, 0, 1) RETURNING id",
            (self.assets[name],))[0]
        (frame,) = self.query("INSERT INTO media_video.frames (shot_id, t_s) VALUES (%s, 0.5) RETURNING id",
                              (shot,))[0]
        for kind, sure, across, down, wide, high in found:
            self.query(
                """INSERT INTO media_video.detections (frame_id, class_name, confidence, cx, cy, w, h, model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'made-up') RETURNING id""",
                (frame, kind, sure, across, down, wide, high))

    def test_a_crop_goes_to_where_the_people_were_found(self):
        self.people("halves", ("person", 0.9, 0.2, 0.6, 0.2, 0.6), ("person", 0.9, 0.4, 0.4, 0.1, 0.4),
                    ("person", 0.2, 0.9, 0.9, 0.5, 0.5), ("dog", 0.9, 0.9, 0.9, 0.5, 0.5))
        clips = [self.clip("halves", 0.0, 1.0), self.clip("wide", 0.0, 1.0), self.clip("halves", 5.0, 6.0)]
        # The nearer person counts for more, by the room they take up. A guess and a dog count for nothing.
        ((clip, (across, down)),) = render.centres(clips).items()
        self.assertEqual(clip, 0)
        self.assertAlmostEqual(across, 0.25)
        self.assertAlmostEqual(down, 0.55)
        timelines.save("made-up-halves", clips[:1], canvas="square", fit="crop")
        self.render("made-up-halves", height=180)
        self.assertTrue(is_red(colour(self.out, 0.5, across=0.8)))

    def test_what_cannot_be_rendered_is_refused_by_name(self):
        with self.assertRaisesRegex(EditError, "no timeline is called 'made-up-nothing'"):
            self.render("made-up-nothing")
        timelines.save("made-up-heard", [self.clip("lapel", 0.0, 1.0)])
        with self.assertRaisesRegex(EditError, "clip 1 of 'made-up-heard' takes its picture from lapel.wav, which"):
            self.render("made-up-heard")
        timelines.save("made-up-late", [self.clip("wide", 0.0, 1.0), self.clip("tall", 2.0, 3.0)])
        with self.assertRaisesRegex(EditError, "clip 2 of 'made-up-late' starts after .* has ended"):
            self.render("made-up-late")
        timelines.save("made-up-blink", [self.clip("wide", 0.0, 0.005)])
        with self.assertRaisesRegex(EditError, "'made-up-blink' is shorter than one frame"):
            self.render("made-up-blink")
        name = self.quay()
        with self.assertRaisesRegex(EditError, "some megabytes"):
            self.render(name, megabytes=0)
        with self.assertRaisesRegex(EditError, "no canvas is called 'round'"):
            self.render(name, canvas="round")
        self.query("UPDATE media.assets SET proxy_path = '/made-up/gone.mp4' WHERE id = %s RETURNING id",
                   (self.assets["tall"],))
        with self.assertRaisesRegex(EditError, "clip 2 of 'made-up-quay-cut' is cut from /made-up/gone.mp4, which"):
            self.render(name)
        self.assertFalse(self.out.exists())
        # The recording itself is still there to be read.
        self.assertTrue(self.render(name, height=90, originals=True, megabytes=0.5).uniform)

    def test_two_shots_are_compared_by_eye(self):
        first, second = (self.query(
            "INSERT INTO media_video.shots (asset_id, idx, start_s, end_s) VALUES (%s, %s, %s, %s) RETURNING id",
            (self.assets["wide"], index, float(index), index + 1.0))[0][0] for index in (0, 1))
        out = render.compare(first, second, self.out, seconds=0.5, height=90)
        self.assertEqual(out, self.out)
        self.assertEqual((ffmpeg.probe(out).width, ffmpeg.probe(out).height), (326, 90))
        self.assertTrue(is_red(colour(out, 0.2, across=0.25)))
        self.assertTrue(is_dark_green(colour(out, 0.2, across=0.75)))
        with self.assertRaisesRegex(EditError, "there is no shot"):
            render.compare(first, "00000000-0000-0000-0000-000000000000", self.out)


class RenderCommandsTest(Library):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.settings.home),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_me_render_says_what_it_made_and_what_it_found(self):
        name = self.quay()
        status, out, err = test_cli.run("render", name, "--out", str(self.out), "--height", "180")
        self.assertEqual((status, err), (0, ""))
        first, sound, looked, path = out.splitlines()
        self.assertRegex(first, r"^made-up-quay-cut: 3 clips, 0:02\.4, 320x180 at 25 frames a second, by \w+$")
        self.assertRegex(sound, r"^  sound in 1 unbroken stretches, moved by \+\d+\.\d to \+\d+\.\d dB$")
        self.assertEqual(looked, "  6 frames looked at: 320x180")
        self.assertEqual(path, str(self.out))
        self.assertTrue(ffmpeg.probe(self.out).has_audio)

    def test_me_render_writes_to_the_work_folder_unless_told_where(self):
        name = self.quay()
        status, out, err = test_cli.run("render", name, "--height", "90", "--silent", "--canvas", "square",
                                        "--fit", "crop", "--fps", "30", "--megabytes", "1")
        self.assertEqual((status, err), (0, ""))
        target = self.settings.work_dir / "renders" / "made-up-quay-cut.mp4"
        self.assertEqual(out.splitlines()[1:], ["  no sound", "  6 frames looked at: 90x90", str(target)])
        self.assertIn("90x90 at 30 frames a second", out)
        self.assertFalse(ffmpeg.probe(target).has_audio)
        status, out, err = test_cli.run("render", name, "--out", str(self.out), "--height", "90", "--as-recorded")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("  sound in 1 unbroken stretches, as loud as it was recorded\n", out)

    def test_me_render_fails_plainly(self):
        self.assertEqual(test_cli.run("render", "made-up-nothing"),
                         (1, "", "media: no timeline is called 'made-up-nothing'; `media timelines` lists them\n"))


class PairsRenderedTest(Media, test_cut.Footage):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.settings.home),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def short(self, name):
        return str(self.shots[name]).replace("-", "")[:12]

    def test_me_pairs_renders_each_pair_side_by_side(self):
        # The made-up reel's shots, moved onto a recording that is really there.
        self.query("UPDATE media.assets SET path = %s WHERE id = %s RETURNING id", (str(self.wide), self.harbour))
        self.query("UPDATE media_video.shots SET start_s = idx * 0.3, end_s = idx * 0.3 + 0.3 "
                   "WHERE asset_id = %s RETURNING id", (self.harbour,))
        folder = self.root / "pairs"
        status, out, err = test_cli.run("pairs", "--count", "2", "--render", str(folder), "--seconds", "0.3")
        self.assertEqual((status, err), (0, ""))
        written = [Path(line.strip()) for line in out.splitlines() if line.strip().endswith(".mp4")
                   and line.strip().startswith("/")]
        self.assertEqual([file.name for file in written],
                         [f"{self.short('red-1')}-{self.short('red-2')}.mp4",
                          f"{self.short('red-3')}-{self.short('red-4')}.mp4"])
        for file in written:
            self.assertEqual(file.parent, folder)
            self.assertEqual((ffmpeg.probe(file).height, ffmpeg.probe(file).has_audio), (360, False))
        # Passages are not pictures, and are listed as they were.
        status, out, err = test_cli.run("pairs", "interview", "--render", str(folder / "talk"))
        self.assertEqual((status, err), (0, ""))
        self.assertFalse((folder / "talk").exists())
