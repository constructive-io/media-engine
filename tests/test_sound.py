"""A cut heard through another recording, and a take's microphones mixed, on a conversation made on the spot."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import derive, takes
from constructive_media.edit import EditError, render, timelines
from constructive_media.edit.timelines import Clip
from constructive_media.takes import TakeError

from .support import (
    HAVE_FFMPEG,
    HAVE_NUMPY,
    TEST_DATABASE_VARIABLE,
    DatabaseTest,
    make_video,
    needs_numpy,
    settings_in,
)
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm
    from constructive_media.edit import sound
    from constructive_media.takes import microphones, mix

    from .test_microphones import record

RATE = mix.RATE if HAVE_NUMPY else 48000


def steady(spans, seconds=4.0, quiet=0.0):
    """A microphone that hears a steady `loud` in the stretches given and `quiet` otherwise."""
    heard = np.full(round(seconds * RATE), float(quiet), dtype=np.float32)
    for start, end, loud in spans:
        heard[round(start * RATE):round(end * RATE)] = loud
    return heard


def at(samples, second):
    return float(samples[round(second * RATE)])


def loud(samples, start, end, rate=RATE):
    """How loud a stretch is: the root of its mean square."""
    part = np.asarray(samples[round(start * rate):round(end * rate)], dtype=np.float64)
    return float(np.sqrt((part ** 2).mean()))


@needs_numpy
class FollowTest(unittest.TestCase):
    def test_whoever_is_speaking_is_up_and_the_other_is_down_and_not_off(self):
        # Each hears the other faintly, a hundredth as loud.
        ada = steady([(0, 2, 0.5), (2, 4, 0.005)])
        ben = steady([(0, 2, 0.005), (2, 4, 0.5)])
        mixed, led = mix.follow([ada, ben], [0.5, 0.5])
        self.assertEqual(len(mixed), len(ada))
        self.assertEqual([round(share, 2) for share in led], [0.5, 0.5])
        down = 10 ** (mix.DUCK_DB / 20)
        self.assertAlmostEqual(at(mixed, 1.0), 0.5 + 0.005 * down, places=4)
        self.assertAlmostEqual(at(mixed, 3.9), 0.5 + 0.005 * down, places=4)
        # How far down is a number to set, and none of it is all the way.
        further, _ = mix.follow([ada, ben], [0.5, 0.5], duck_db=-30.0)
        self.assertAlmostEqual(at(further, 1.0), 0.5 + 0.005 * 10 ** (-30 / 20), places=4)
        self.assertGreater(at(further, 1.0), 0.5)

    def test_a_fader_comes_up_a_little_early_and_fast_and_goes_down_slowly(self):
        ada = steady([(0, 2, 0.5)])
        ben = steady([(2, 4, 0.5)])
        mixed, _ = mix.follow([ada, ben], [0.5, 0.5])
        down = 10 ** (mix.DUCK_DB / 20)
        # Ben's first word is not lost: his microphone is already on its way up when he starts.
        self.assertGreater(at(mixed, 2.005), 0.5 * down * 1.5)
        self.assertGreater(at(mixed, 2.25), 0.5 * 0.95)
        # And nothing jumps but the voices themselves.
        steps = np.abs(np.diff(mixed))
        steps[round(2.0 * RATE) - 2:round(2.0 * RATE) + 2] = 0
        self.assertLess(float(steps.max()), 0.001)
        # Ada's goes down slowly: the room heard through it is still well up half a second on.
        room, _ = mix.follow([steady([(0, 2, 0.5)], quiet=0.02), ben], [0.5, 0.5])
        self.assertGreater(at(room, 2.5) - 0.5, 0.02 * down * 1.4)
        self.assertLess(at(room, 2.5) - 0.5, 0.02 * 0.6)
        self.assertAlmostEqual(at(room, 3.9) - 0.5, 0.02 * down, places=3)

    def test_through_a_pause_the_last_to_speak_stays_up(self):
        ada = steady([(0, 1, 0.5)], quiet=0.002)
        ben = steady([], quiet=0.003)      # the room is a little louder in his, and that is not speaking
        mixed, led = mix.follow([ada, ben], [0.5, 0.5])
        self.assertEqual(led, [1.0, 0.0])
        self.assertAlmostEqual(at(mixed, 3.0), 0.002 + 0.003 * 10 ** (mix.DUCK_DB / 20), places=5)
        # Before anyone has spoken, the first one to speak is already up.
        late, led = mix.follow([steady([(2, 4, 0.5)], quiet=0.002), ben], [0.5, 0.5])
        self.assertEqual(led, [1.0, 0.0])
        self.assertAlmostEqual(at(late, 1.0), 0.002 + 0.003 * 10 ** (mix.DUCK_DB / 20), places=5)

    def test_two_who_speak_at_once_are_both_up(self):
        ada = steady([(0, 4, 0.5)])
        ben = steady([(1, 3, 0.4)])
        mixed, led = mix.follow([ada, ben], [0.5, 0.5])
        self.assertEqual(led, [1.0, 0.0])
        self.assertAlmostEqual(at(mixed, 2.0), 0.9, places=3)
        # Further under than that and he is the other one, turned down.
        under, _ = mix.follow([ada, steady([(1, 3, 0.2)])], [0.5, 0.5])
        self.assertAlmostEqual(at(under, 2.0), 0.5 + 0.2 * 10 ** (mix.DUCK_DB / 20), places=3)

    def test_everyone_is_brought_to_one_level_by_a_bounded_amount(self):
        # Ben's recorder was turned down to a quarter, and each is judged against its own loud level.
        ada = steady([(0, 2, 0.5)])
        ben = steady([(2, 4, 0.125)])
        mixed, led = mix.follow([ada, ben], [0.5, 0.125])
        self.assertEqual([round(share, 2) for share in led], [0.5, 0.5])
        self.assertAlmostEqual(at(mixed, 1.0), at(mixed, 3.5), places=4)
        # One turned down a thousandfold is not brought all the way up, with its hiss.
        faint, _ = mix.follow([ada, steady([(2, 4, 0.0005)])], [0.5, 0.0005])
        self.assertAlmostEqual(at(faint, 3.5), 0.0005 * 10 ** (mix.EVEN_DB / 20), places=5)

    def test_a_mix_needs_sounds_and_a_loud_level_for_each(self):
        with self.assertRaisesRegex(ValueError, "a loud level for each"):
            mix.follow([steady([])], [0.5, 0.5])
        with self.assertRaisesRegex(ValueError, "needs sounds"):
            mix.follow([], [])
        # Sounds of different lengths are mixed for as long as the shortest; under one hop there is nothing.
        mixed, _ = mix.follow([steady([(0, 2, 0.5)]), steady([], seconds=3.0)], [0.5, 0.5])
        self.assertEqual(len(mixed), 3 * RATE)
        nothing, led = mix.follow([np.zeros(100), np.zeros(100)], [0.5, 0.5])
        self.assertEqual((len(nothing), led), (100, [0.0, 0.0]))

    def test_a_long_stretch_is_mixed_a_minute_at_a_time_and_comes_out_the_same(self):
        ada = steady([(0, 2, 0.5), (4, 5.007, 0.4)], seconds=5.007, quiet=0.004)
        ben = steady([(2, 4, 0.5)], seconds=5.007, quiet=0.003)
        whole, led = mix.follow([ada, ben], [0.5, 0.5])
        with mock.patch.object(mix, "BLOCK", 7):
            pieces, again = mix.follow([ada, ben], [0.5, 0.5])
        self.assertEqual(led, again)
        self.assertTrue(np.allclose(whole, pieces, atol=1e-7))
        # The last few samples, short of one more gain, are held at the last.
        self.assertEqual(len(whole), len(ada))
        self.assertAlmostEqual(float(whole[-1]), 0.4 + 0.003 * 10 ** (mix.DUCK_DB / 20), places=3)


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class Table(DatabaseTest):
    """The made-up conversation as a take: a camera, a recorder on the table, and a microphone each on Ada and Ben.

    Ada speaks from 2, 12, 22 and 32 seconds into the take and Ben from 7, 17
    and 27, three seconds each. The camera's own sound is one steady tone.
    """

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        make_video(cls.root / "camera.mp4", seconds=15.0)
        record(cls.root / "table.wav", 45.0, 0.0, None)
        record(cls.root / "ada-collar.wav", 45.0, 0.0, "ada")
        # Ben's was started a second and a half late, stopped early, and is turned well down.
        record(cls.root / "ben-collar.wav", 40.0, 1.5, "ben", gain=0.2)
        make_video(cls.root / "elsewhere.mp4", seconds=1.0)
        cls.settings = settings_in(cls.root / "home")

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.root / "home"),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        ((self.take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-table', 'made-up') RETURNING id")
        self.camera = self.join("camera.mp4", "camera", primary=True)
        self.table = self.join("table.wav", "ambient")
        self.ada = self.join("ada-collar.wav", "lav")
        self.ben = self.join("ben-collar.wav", "lav", offset=1.5, seconds=40.0)
        self.elsewhere = self.asset("elsewhere.mp4", 3.0)
        self.take = takes.find("made-up-table")
        self.out = self.root / "renders" / f"{self._testMethodName}.mp4"

    def asset(self, name, seconds):
        video = name.endswith(".mp4")
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s, fps, width, height)
               VALUES (%s, %s, %s, true, 'made-up', %s, %s, %s, %s) RETURNING id""",
            ("video" if video else "audio", str(self.root / name), f"made-up-{name}", seconds,
             25 if video else None, 160 if video else None, 90 if video else None))
        return asset_id

    def join(self, name, role, *, primary=False, offset=0.0, placed=True, seconds=45.0):
        asset_id = self.asset(name, seconds)
        self.query(
            """INSERT INTO media.take_assets (take_id, asset_id, role, is_primary, offset_s, offset_method)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING asset_id""",
            (self.take_id, asset_id, role, primary, offset, "words" if placed and not primary else None))
        return asset_id

    def cut(self, name="made-up-talk", spans=((1.0, 11.0), (16.0, 24.0)), **options):
        """A cut from the camera: by default Ada's and Ben's first lines, then Ben's second and Ada's third."""
        timelines.save(name, [Clip(self.camera, start, end) for start, end in spans], project="made-up", **options)
        return name

    def heard(self, name):
        """What each clip of a cut is heard through: the file, and from when to when."""
        names = {asset_id: Path(path).name for asset_id, path in self.query("SELECT id, path FROM media.assets")}
        return [(names[clip.sound], round(clip.sound_start_s, 3), round(clip.sound_end_s, 3)) if clip.sound else None
                for clip in timelines.load(name).clips]

    def used(self, path):
        rows = self.query(
            """SELECT a.path, s.role, s.start_s, s.end_s
                 FROM media.derivation_sources s
                 JOIN media.derivations d ON d.id = s.derivation_id
                 JOIN media.assets made ON made.id = d.output_id
                 JOIN media.assets a ON a.id = s.source_id
                WHERE made.path = %s ORDER BY a.path, s.role""", (str(path),))
        return [(Path(file).name, role, round(start, 2), round(end, 2)) for file, role, start, end in rows]


class MixerTest(Table):
    def test_a_stretch_of_a_take_is_its_worn_microphones_mixed(self):
        mixer = mix.Mixer(self.take)
        self.assertTrue(mixer.able)
        self.assertEqual([one.name for one in mixer.microphones], ["ada-collar.wav", "ben-collar.wav"])
        made = mixer.mix(1.0, 11.0, self.root / "mixed" / "first.wav")
        self.assertEqual((made.file, made.seconds), (self.root / "mixed" / "first.wav", 10.0))
        samples = pcm.read(made.file, stereo=False)
        self.assertEqual((pcm.seconds(made.file), len(samples)), (10.0, 10 * RATE))
        # Ada speaks from 1 to 4 of it and Ben from 6 to 9, and until he does hers is the one that is up.
        self.assertEqual({who: round(share, 1) for who, share in made.led.items()},
                         {"ada-collar.wav": 0.6, "ben-collar.wav": 0.4})
        # Ben's recorder was turned down to a fifth, and he is as loud in the mix as she is.
        hers, his, between = loud(samples, 1.5, 3.5), loud(samples, 6.5, 8.5), loud(samples, 4.5, 5.5)
        self.assertLess(abs(20 * np.log10(his / hers)), 3.0)
        self.assertLess(between, hers / 20)
        # What was read is written down in each recording's own time: his started a second and a half in.
        self.assertEqual([(asset, round(start, 2), round(end, 2)) for asset, start, end in made.heard],
                         [(self.ada, 1.0, 11.0), (self.ben, 0.0, 9.5)])

    def test_a_microphone_that_started_late_is_silent_until_it_did(self):
        made = mix.Mixer(self.take).mix(0.0, 6.0, self.root / "mixed" / "opening.wav")
        self.assertEqual([(asset, round(start, 2), round(end, 2)) for asset, start, end in made.heard],
                         [(self.ada, 0.0, 6.0), (self.ben, 0.0, 4.5)])
        self.assertEqual(round(made.led["ada-collar.wav"], 1), 1.0)

    def test_where_there_is_nothing_to_follow_nothing_is_mixed(self):
        mixer = mix.Mixer(self.take)
        # Ben's stopped 41.5 seconds in: after that only one was recording.
        self.assertIsNone(mixer.mix(42.0, 44.5, self.root / "mixed" / "late.wav"))
        self.assertFalse((self.root / "mixed" / "late.wav").exists())
        # A take with one microphone has nothing to choose between.
        self.query("UPDATE media.take_assets SET role = 'board' WHERE asset_id = %s RETURNING asset_id", (self.ben,))
        alone = mix.Mixer(self.take)
        self.assertFalse(alone.able)
        self.assertIsNone(alone.mix(1.0, 11.0, self.root / "mixed" / "alone.wav"))

    def test_a_recording_is_a_microphone_when_it_is_worn(self):
        microphones.wear("made-up-table", "table", "Cy", new=True)
        self.assertEqual([one.name for one in mix.Mixer(self.take).microphones],
                         ["table.wav", "ada-collar.wav", "ben-collar.wav"])

    def test_a_microphone_whose_file_has_gone_is_named(self):
        self.query("UPDATE media.assets SET path = %s WHERE id = %s RETURNING id",
                   (str(self.root / "gone.wav"), self.ben))
        with self.assertRaisesRegex(TakeError, "gone.wav is not where it was"):
            mix.Mixer(self.take)


class RenderMixTest(Table):
    def render(self, name, **options):
        return render.render(name, self.out, settings=self.settings, height=90, **options)

    def sound(self, seconds):
        return mix.stretch(self.out, 0.0, seconds)

    def test_a_cut_is_rendered_with_its_microphones_mixed(self):
        made = self.render(self.cut(), mix=True)
        self.assertEqual((made.clips, len(made.gains), made.mixed, round(made.mixed_s, 2)), (2, 2, 2, 18.0))
        # Ada leads the first clip until Ben speaks, six seconds in. Ben speaks first in the second.
        self.assertEqual([(who, round(seconds)) for who, seconds in made.led],
                         [("ben-collar.wav", 10), ("ada-collar.wav", 8)])
        self.assertLess(abs(made.adrift_s), 0.1)
        # What is heard is the conversation and not the camera's steady tone: loud in a line, quiet between.
        heard = self.sound(10.0)
        self.assertGreater(loud(heard, 1.5, 3.5), 10 * loud(heard, 4.5, 5.5))
        self.assertLess(abs(20 * np.log10(loud(heard, 6.5, 8.5) / loud(heard, 1.5, 3.5))), 3.0)
        # The sound was made from the microphones and not the camera, and the mixed files are gone.
        self.assertEqual(self.used(self.out), [
            ("ada-collar.wav", "sound", 1.0, 24.0), ("ben-collar.wav", "sound", 0.0, 22.5),
            ("camera.mp4", "picture", 1.0, 24.0)])
        params = derive.lineage(self.out).params
        self.assertEqual((params["mix"], params["duck_db"]), (True, mix.DUCK_DB))
        self.assertEqual(list((self.settings.work_dir / "renders").glob("mix-*")), [])

    def test_without_mix_a_cut_is_heard_as_it_was_cut(self):
        made = self.render(self.cut())
        self.assertEqual((made.mixed, made.mixed_s, made.led), (0, 0.0, ()))
        heard = self.sound(10.0)
        self.assertLess(loud(heard, 1.5, 3.5), 2 * loud(heard, 4.5, 5.5))
        self.assertNotIn("mix", derive.lineage(self.out).params)

    def test_a_stretch_the_microphones_do_not_cover_is_left_as_cut(self):
        made = self.render(self.cut(spans=((1.0, 5.0), (42.0, 44.5))), mix=True, duck_db=-20.0)
        self.assertEqual((len(made.gains), made.mixed, round(made.mixed_s, 2)), (2, 1, 4.0))
        self.assertEqual(derive.lineage(self.out).params["duck_db"], -20.0)
        self.assertEqual(self.used(self.out)[:2],
                         [("ada-collar.wav", "sound", 1.0, 5.0), ("ben-collar.wav", "sound", 0.0, 3.5)])

    def test_a_cut_with_nothing_to_mix_is_refused(self):
        timelines.save("made-up-elsewhere", [Clip(self.elsewhere, 0.0, 2.0)], project="made-up")
        with self.assertRaisesRegex(EditError, "nothing in 'made-up-elsewhere' has two worn microphones"):
            self.render("made-up-elsewhere", mix=True)
        with self.assertRaisesRegex(EditError, "a render without sound has nothing to mix"):
            self.render(self.cut(), mix=True, silent=True)
        self.assertFalse(self.out.exists())
        self.assertEqual(list((self.settings.work_dir / "renders").glob("mix-*")), [])

    def test_a_cut_heard_through_another_recording_is_mixed_all_the_same(self):
        # Heard through the table recorder, a stretch is still the same moment of the same take.
        sound.relink(self.cut(), "made-up-talk-table", "table")
        made = self.render("made-up-talk-table", mix=True)
        self.assertEqual((made.mixed, round(made.mixed_s, 2)), (2, 18.0))

    def test_media_render_says_what_was_mixed(self):
        status, printed, complaint = run("render", self.cut(), "--mix", "--duck", "9", "--height", "90",
                                         "--out", str(self.out))
        self.assertEqual((status, complaint), (0, ""))
        self.assertIn("  2 of them mixed from worn microphones, 0:18.0 in all: ben-collar.wav led 0:10.0, "
                      "ada-collar.wav led 0:08.0\n", printed)
        self.assertNotIn("left as cut", printed)
        self.assertEqual(derive.lineage(self.out).params["duck_db"], -9.0)
        status, printed, complaint = run("render", self.cut("made-up-end", spans=((1.0, 5.0), (42.0, 44.5))),
                                         "--mix", "--height", "90", "--out", str(self.out))
        self.assertEqual(status, 0)
        self.assertIn("  1 left as cut: fewer than two worn microphones were recording through them", printed)
        timelines.save("made-up-elsewhere", [Clip(self.elsewhere, 0.0, 2.0)], project="made-up")
        status, printed, complaint = run("render", "made-up-elsewhere", "--mix", "--out", str(self.out))
        self.assertEqual((status, printed), (1, ""))
        self.assertIn("media: nothing in 'made-up-elsewhere' has two worn microphones", complaint)
        self.assertIn("`media wear` says who wore which", complaint)

    def test_media_render_takes_a_fade_and_a_highpass(self):
        with mock.patch.object(render, "render", wraps=render.render) as rendered:
            status, _, _ = run("render", self.cut(), "--fade", "0.05", "--highpass", "0", "--height", "90",
                               "--out", str(self.out))
        self.assertEqual(status, 0)
        asked = rendered.call_args.kwargs
        self.assertEqual((asked["fade_s"], asked["highpass_hz"], asked["mix"]), (0.05, 0.0, False))


class RelinkTest(Table):
    def test_a_cut_is_stored_again_heard_through_another_recording(self):
        made = sound.relink(self.cut(), "made-up-talk-table", "table")
        self.assertEqual((made.timeline.name, made.changed, made.kept, made.through),
                         ("made-up-talk-table", 2, (), ("table.wav",)))
        self.assertEqual(self.heard("made-up-talk-table"), [("table.wav", 1.0, 11.0), ("table.wav", 16.0, 24.0)])
        # The picture, the clips and their order are as they were, and so is the cut it was made from.
        again, before = timelines.load("made-up-talk-table"), timelines.load("made-up-talk")
        self.assertEqual([(clip.picture, clip.start_s, clip.end_s) for clip in again.clips],
                         [(clip.picture, clip.start_s, clip.end_s) for clip in before.clips])
        self.assertEqual(self.heard("made-up-talk"), [None, None])
        self.assertEqual((again.kind, again.project, again.duration_s), (before.kind, "made-up", 18.0))
        self.assertEqual(again.params["sound"], {"from": "made-up-talk", "recording": "table", "take": None})
        self.assertEqual(self.query(
            """SELECT p.name FROM media_video.timelines t JOIN media_video.timelines p ON p.id = t.parent_id
                WHERE t.name = 'made-up-talk-table'"""), [("made-up-talk",)])

    def test_the_sound_is_moved_to_the_same_moment_in_the_recording(self):
        # Ben's microphone started a second and a half into the take, and stopped at 41.5.
        made = sound.relink(self.cut(spans=((1.0, 11.0), (16.0, 24.0), (38.0, 43.0))), "made-up-talk-ben", "BEN")
        why = "was said while ben-collar.wav was not recording"
        self.assertEqual((made.changed, made.kept, made.through), (1, ((1, why), (3, why)), ("ben-collar.wav",)))
        self.assertEqual(self.heard("made-up-talk-ben"), [None, ("ben-collar.wav", 14.5, 22.5), None])
        # A cut already heard through another recording is found from where it is heard.
        sound.relink("made-up-talk-ben", "made-up-talk-ada", "ada-collar.wav")
        self.assertEqual(self.heard("made-up-talk-ada"),
                         [("ada-collar.wav", 1.0, 11.0), ("ada-collar.wav", 16.0, 24.0), ("ada-collar.wav", 38.0, 43.0)])
        sound.relink("made-up-talk-ada", "made-up-talk-ben-again", "ben-collar", take="made-up-table")
        self.assertEqual(self.heard("made-up-talk-ben-again"),
                         [("ada-collar.wav", 1.0, 11.0), ("ben-collar.wav", 14.5, 22.5), ("ada-collar.wav", 38.0, 43.0)])
        self.assertEqual(timelines.load("made-up-talk-ben-again").params["sound"],
                         {"from": "made-up-talk-ada", "recording": "ben-collar", "take": "made-up-table"})

    def test_back_to_its_own_recording_a_clip_has_no_sound_apart(self):
        sound.relink(self.cut(), "made-up-talk-table", "table")
        made = sound.relink("made-up-talk-table", "made-up-talk-camera", "camera")
        self.assertEqual((made.changed, made.through), (2, ("camera.mp4",)))
        self.assertEqual(self.heard("made-up-talk-camera"), [None, None])
        self.assertEqual(timelines.load("made-up-talk-camera").duration_s, 18.0)

    def test_under_its_own_name_a_cut_is_changed_where_it_is(self):
        made = sound.relink(self.cut(), "made-up-talk", "table")
        self.assertEqual((made.timeline.name, made.changed), ("made-up-talk", 2))
        self.assertEqual(self.heard("made-up-talk"), [("table.wav", 1.0, 11.0), ("table.wav", 16.0, 24.0)])
        self.assertEqual(self.query("SELECT count(*) FROM media_video.timelines WHERE name LIKE 'made-up-talk%%'"),
                         [(1,)])

    def test_a_clip_from_outside_the_take_is_left_as_it_was(self):
        timelines.save("made-up-both", [Clip(self.camera, 1.0, 5.0), Clip(self.elsewhere, 0.0, 2.0)],
                       project="made-up")
        made = sound.relink("made-up-both", "made-up-both-table", "table")
        self.assertEqual((made.changed, made.kept), (1, ((2, "is from a recording that is in no take"),)))
        self.assertEqual(self.heard("made-up-both-table"), [("table.wav", 1.0, 5.0), None])

    def test_what_cannot_be_heard_through_a_recording_is_refused_and_says_why(self):
        self.cut()
        with self.assertRaisesRegex(EditError, "no clip of 'made-up-talk' could be heard through 'boom': every one "
                                               "is in take 'made-up-table', where nothing is called 'boom'"):
            sound.relink("made-up-talk", "made-up-talk-boom", "boom")
        with self.assertRaisesRegex(EditError, "where 'collar' could be any of 2 recordings"):
            sound.relink("made-up-talk", "made-up-talk-collar", "collar")
        with self.assertRaisesRegex(EditError, "every one is heard through it already"):
            sound.relink("made-up-talk", "made-up-talk-camera", "camera")
        with self.assertRaisesRegex(TakeError, "no take is called 'made-up-bench'"):
            sound.relink("made-up-talk", "made-up-talk-table", "table", take="made-up-bench")
        with self.assertRaises(TakeError):
            sound.relink("made-up-talk", "made-up-talk-boom", "boom", take="made-up-table")
        with self.assertRaisesRegex(EditError, "made-up-nothing"):
            sound.relink("made-up-nothing", "made-up-talk-table", "table")
        self.assertEqual(self.query("SELECT name FROM media_video.timelines WHERE name LIKE 'made-up-%%'"),
                         [("made-up-talk",)])

    def test_a_recording_with_no_place_in_the_take_cannot_be_heard_through(self):
        self.join("ada-collar.wav".replace("ada", "cy"), "lav", placed=False)
        with self.assertRaisesRegex(EditError, "cannot be found in cy-collar.wav, which has no place in the take "
                                               "yet; `media sync` gives it one"):
            sound.relink(self.cut(), "made-up-talk-cy", "cy-collar")
        self.query("UPDATE media.assets SET has_audio = false WHERE id = %s RETURNING id", (self.table,))
        with self.assertRaisesRegex(EditError, "table.wav has no sound to hear a cut through"):
            sound.relink("made-up-talk", "made-up-talk-table", "table")

    def test_a_relinked_cut_is_rendered_from_the_recording(self):
        sound.relink(self.cut(), "made-up-talk-ada", "ada-collar")
        render.render("made-up-talk-ada", self.out, settings=self.settings, height=90)
        self.assertEqual(self.used(self.out), [("ada-collar.wav", "sound", 1.0, 24.0),
                                               ("camera.mp4", "picture", 1.0, 24.0)])
        heard = mix.stretch(self.out, 0.0, 10.0)
        self.assertGreater(loud(heard, 1.5, 3.5), 10 * loud(heard, 4.5, 5.5))

    def test_media_relink_says_what_is_heard_through_what(self):
        self.cut(spans=((1.0, 11.0), (16.0, 24.0), (38.0, 43.0)))
        status, printed, complaint = run("relink", "made-up-talk", "made-up-talk-ben", "ben-collar")
        self.assertEqual((status, complaint), (0, ""))
        self.assertEqual(printed.splitlines(), [
            "made-up-talk-ben: 1 of 3 clips, 0:23.0, now heard through ben-collar.wav",
            "  clip 1 is as it was: it was said while ben-collar.wav was not recording",
            "  clip 3 is as it was: it was said while ben-collar.wav was not recording",
        ])
        status, printed, _ = run("relink", "made-up-talk", "made-up-talk-table", "table", "--take", "made-up-table",
                                 "--dry-run")
        self.assertEqual(status, 0)
        self.assertEqual(printed.splitlines(), ["made-up-talk-table: 3 of 3 clips, 0:23.0, now heard through table.wav",
                                                "nothing was written"])
        self.assertEqual(self.query("SELECT count(*) FROM media_video.timelines WHERE name = 'made-up-talk-table'"),
                         [(0,)])
        status, printed, complaint = run("relink", "made-up-talk", "made-up-talk-boom", "boom")
        self.assertEqual((status, printed), (1, ""))
        self.assertIn("media: no clip of 'made-up-talk' could be heard through 'boom'", complaint)
