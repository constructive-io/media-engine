"""What is measured in the sound and the movement, on signals made on the spot."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import ffmpeg, ingest
from constructive_media.edit import scores

from .support import HAVE_FFMPEG, HAVE_NUMPY, TEST_DATABASE_VARIABLE, DatabaseTest, needs_numpy, settings_in, write_lines
from .test_cli import run
from .test_edit import Scored

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import audible, laughter, pcm
    from constructive_media.video import coordination, loudness

RATE = 16000


def voice(seconds, bursts, *, level=0.3, pitch=180.0):
    """A voiced sound whose loudness rises and falls `bursts` times a second: 3 as speech does, 6 as a laugh."""
    at = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * pitch * part * at) / part for part in (1, 2, 3))
    return level * tone * (0.5 + 0.5 * np.sin(2 * np.pi * bursts * at)) ** 2


def talk(seconds=40.0, laugh_at=None, hiss=0.002):
    """Somebody talking in a quiet room, and laughing for three seconds where asked."""
    sound = voice(seconds, 3.0)
    if laugh_at is not None:
        sound[int(laugh_at * RATE):int((laugh_at + 3) * RATE)] = voice(3, 6.0, level=0.4)
    return sound + np.random.default_rng(7).normal(0, hiss, len(sound))


def film(follows=0, seed=3, pictures=40, size=(48, 27), other=None):
    """Pictures of a lit red block and a grey one on a dark wall.

    The grey block brightens and dims with the red one, `follows` pictures
    after it, or with `other` to a rhythm of its own.
    """
    width, height = size
    moves = np.abs(np.random.default_rng(seed).normal(0, 1, pictures + 20)).cumsum()
    alone = np.abs(np.random.default_rng(other or 0).normal(0, 1, pictures + 20)).cumsum()
    frames = np.full((pictures, height, width, 3), 0.15, dtype=np.float32)
    for n in range(pictures):
        frames[n, 2:height // 3, 2:width // 2] = (0.7 + 0.2 * np.sin(moves[n + 10]), 0.05, 0.05)
        grey = 0.5 + 0.2 * np.sin(alone[n + 10] if other else moves[n + 10 - follows])
        frames[n, height // 2:height - 2, width // 2:width - 2] = (grey, grey, grey)
    return frames


@needs_numpy
class LoudnessTest(unittest.TestCase):
    def setUp(self):
        self.rms = np.full(300 * loudness.RATE, 0.01)
        self.rms[150 * loudness.RATE:155 * loudness.RATE] = 0.02
        self.peak = self.rms * 1.5

    def test_a_shot_is_measured_against_the_minute_around_it(self):
        loud = loudness.level(self.rms, self.peak, 150.0, 155.0)
        self.assertAlmostEqual(loud.loudness, -33.98, places=2)
        self.assertAlmostEqual(loud.peak_db, -30.46, places=2)
        # Twice as loud is six decibels, less what the shot itself adds to its surroundings.
        self.assertAlmostEqual(loud.lift_db, 5.12, places=2)
        quiet = loudness.level(self.rms, self.peak, 10.0, 15.0)
        self.assertEqual((round(quiet.loudness, 2), round(quiet.lift_db, 2)), (-40.0, 0.0))

    def test_a_shot_that_is_most_of_its_file_is_set_against_all_of_it(self):
        rms = np.concatenate([np.full(400, 0.01), np.full(600, 0.04)])
        self.assertGreater(loudness.level(rms, rms, 4.0, 10.0).lift_db, 1.0)
        self.assertLess(loudness.level(rms, rms, 0.0, 4.0).lift_db, -9.0)

    def test_a_shot_outside_the_sound_is_not_measured(self):
        self.assertIsNone(loudness.level(self.rms, self.peak, 400.0, 410.0))

    def test_silence_is_a_number_and_not_an_error(self):
        self.assertEqual(loudness.decibels(0.0), -120.0)
        self.assertAlmostEqual(loudness.decibels(0.5), -6.02, places=2)


@needs_numpy
class AudibleTest(unittest.TestCase):
    def setUp(self):
        at = np.arange(2 * RATE) / RATE
        self.sound = np.random.default_rng(7).normal(0, 0.01, 10 * RATE)
        self.sound[2 * RATE:4 * RATE] += 0.1 * np.sin(2 * np.pi * 300 * at)
        self.sound[6 * RATE:8 * RATE] = np.clip(self.sound[6 * RATE:8 * RATE] + 3 * np.sin(2 * np.pi * 300 * at), -1, 1)

    def test_a_line_is_measured_against_the_recordings_own_noise(self):
        clear, loud = audible.measure(self.sound, [(2.0, 4.0), (6.0, 8.0)], RATE)
        self.assertAlmostEqual(clear.noise_db, -40.0, delta=1.0)
        # A tone a tenth of full scale is seventeen decibels over noise a hundredth of it.
        self.assertAlmostEqual(clear.snr_db, 17.0, delta=1.0)
        self.assertAlmostEqual(clear.rms_db, -23.0, delta=0.5)
        self.assertEqual(clear.clipped_ratio, 0.0)
        self.assertEqual(loud.noise_db, clear.noise_db)
        self.assertGreater(loud.snr_db, 35.0)

    def test_what_was_cut_off_at_the_top_of_the_scale_is_counted(self):
        (_, loud) = audible.measure(self.sound, [(2.0, 4.0), (6.0, 8.0)], RATE)
        self.assertGreater(loud.clipped_ratio, 0.5)

    def test_noise_is_high_and_a_low_tone_is_not(self):
        hiss, tone = audible.measure(self.sound, [(0.0, 2.0), (6.0, 8.0)], RATE)
        self.assertAlmostEqual(hiss.hf_ratio, 0.5, delta=0.05)
        self.assertLess(tone.hf_ratio, 0.1)

    def test_a_line_too_short_or_outside_the_sound_is_not_measured(self):
        self.assertEqual(audible.measure(self.sound, [(9.99, 10.0), (20.0, 21.0)], RATE), [None, None])
        self.assertEqual(audible.measure(self.sound[:500], [(0.0, 1.0)], RATE), [None])


@needs_numpy
class LaughterTest(unittest.TestCase):
    def test_a_laugh_is_found_among_speech(self):
        (laugh,) = laughter.find(talk(60.0, laugh_at=30.0), RATE)
        self.assertGreaterEqual(laugh.start_s, 29.0)
        self.assertLessEqual(laugh.end_s, 34.0)
        self.assertGreater(laugh.confidence, 0.8)
        self.assertAlmostEqual(laugh.bursts_hz, 6.0, delta=0.5)

    def test_speech_alone_is_not_laughter(self):
        self.assertEqual(laughter.find(talk(60.0), RATE), [])

    def test_a_recording_that_bursts_throughout_is_not_one_long_laugh(self):
        # Each recording is judged against itself: where everything looks
        # like laughter, only its rarest moments are candidates, and a long
        # run of them is less sure than a short one.
        found = laughter.find(voice(60.0, 6.0), RATE)
        self.assertLess(sum(laugh.end_s - laugh.start_s for laugh in found), 12.0)

    def test_a_stricter_listener_keeps_less(self):
        self.assertEqual(laughter.find(talk(60.0, laugh_at=30.0), RATE, min_confidence=0.99), [])

    def test_under_a_second_there_is_nothing_to_hear(self):
        self.assertEqual(laughter.find(np.zeros(RATE // 2), RATE), [])
        self.assertEqual(laughter.find(np.zeros(RATE * 30), RATE), [])


@needs_numpy
class CoordinationTest(unittest.TestCase):
    def test_what_moves_in_step_with_the_lit_part_scores_highest(self):
        seen = coordination.together(film())
        self.assertGreater(seen.corr, 0.99)
        self.assertEqual((seen.lag_s, seen.best_at_s, seen.n_frames), (0.0, 0.0, 40))
        self.assertGreater(seen.lit_pixels, 50)
        self.assertGreater(seen.lit_motion, 0)
        self.assertGreater(seen.scene_motion, 0)

    def test_which_moved_first_is_kept(self):
        after = coordination.together(film(follows=3))
        self.assertGreater(after.corr, 0.99)
        self.assertAlmostEqual(after.lag_s, -0.3)        # the lit part led, by three pictures
        before = coordination.together(film(follows=-3))
        self.assertAlmostEqual(before.lag_s, 0.3)        # the scene led, and the light answered

    def test_movement_to_another_rhythm_scores_lower(self):
        self.assertLess(coordination.together(film(other=11)).corr, 0.8)

    def test_the_best_four_seconds_of_a_long_shot_are_the_ones_scored(self):
        apart, close = film(other=11, pictures=60), film(pictures=60)
        seen = coordination.together(np.concatenate([apart, close]))
        self.assertGreater(seen.corr, 0.99)
        self.assertGreaterEqual(seen.best_at_s, 6.0)

    def test_with_nothing_lit_there_is_no_answer(self):
        dark = coordination.together(np.full((40, 27, 48, 3), 0.2))
        self.assertEqual((dark.corr, dark.lag_s, dark.lit_pixels, dark.n_frames), (None, None, 0.0, 40))
        still = film()
        still[:] = still[0]
        self.assertIsNone(coordination.together(still).corr)

    def test_under_a_second_is_too_little(self):
        self.assertIsNone(coordination.together(film(pictures=5)))


def make_film(path, frames, sound, *, fps=coordination.FPS if HAVE_NUMPY else 10):
    """Encode pictures from 0 to 1, with a sound under them."""
    raw, wav = path.with_suffix(".rgb"), path.with_suffix(".wav")
    raw.write_bytes((np.clip(frames, 0, 1) * 255).astype(np.uint8).tobytes())
    pcm.write(wav, sound, RATE, bits=16)
    height, width = frames.shape[1:3]
    ffmpeg.encode(
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", raw, "-i", wav,
        "-c:v", "{vcodec}", "-b:v", "2M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
    )
    raw.unlink()
    wav.unlink()
    return path


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class MeasuredTest(DatabaseTest):
    """A folder with a conversation somebody laughs in, and a film whose last third is loud."""

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.footage = cls.root / "footage"
        cls.footage.mkdir()
        pcm.write(cls.footage / "talk.wav", talk(40.0, laugh_at=20.0, hiss=0.01), RATE, bits=16)
        hum = 0.02 * np.sin(2 * np.pi * 220 * np.arange(6 * RATE) / RATE)
        hum[4 * RATE:] *= 8
        make_film(cls.footage / "show.mp4", film(pictures=60, size=(192, 108)), hum)
        cls.settings = settings_in(cls.root / "home")

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.root / "home"),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        ingest.ingest(self.footage, "demo", settings=self.settings)
        assets = dict(self.query("SELECT regexp_replace(path, '.*/', ''), id FROM media.assets WHERE project = 'demo'"))
        self.talk, self.show = assets["talk.wav"], assets["show.mp4"]
        self.shots = [shot_id for (shot_id,) in self.query(
            """INSERT INTO media_video.shots (asset_id, idx, start_s, end_s)
               VALUES (%s, 0, 0, 2), (%s, 1, 2, 4), (%s, 2, 4, 6) RETURNING id""", (self.show,) * 3)]

    def test_each_shot_is_given_its_level_and_its_lift(self):
        self.assertEqual(loudness.measure("show"), [("show.mp4", 3)])
        (first, _), (_, _), (last, peak) = self.query(
            "SELECT lift_db, peak_db FROM media_video.shots WHERE asset_id = %s ORDER BY idx", (self.show,))
        self.assertGreater(last, 3.0)
        self.assertLess(first, -6.0)
        self.assertAlmostEqual(peak, -15.9, delta=1.5)
        self.assertEqual(loudness.measure("show"), [])
        self.assertEqual(loudness.measure("show", redo=True), [("show.mp4", 3)])
        self.assertEqual(loudness.measure("no-such-file", redo=True), [])

    def test_loudness_is_turned_into_a_score_the_ear_gave(self):
        loudness.measure("show")
        scores.run("demo")
        found = dict(self.query(
            "SELECT subject_id, value FROM media_video.scores WHERE name = 'energy_lift' AND modality = 'audio'"))
        self.assertEqual(found[self.shots[0]], 0.0)
        self.assertGreater(found[self.shots[2]], 0.5)

    def test_each_shot_is_watched_for_what_moves_with_the_light(self):
        whole = self.query("INSERT INTO media_video.shots (asset_id, idx, start_s, end_s) VALUES (%s, 3, 0, 6) "
                           "RETURNING id", (self.show,))[0][0]
        self.query("UPDATE media_video.shots SET is_black = true WHERE id = %s RETURNING id", (self.shots[1],))
        self.assertEqual(coordination.measure("show"), [("show.mp4", 3, 3)])
        ((corr, lag, pixels, frames),) = self.query(
            "SELECT corr, lag_s, beam_pixels, n_frames FROM media_video.coordination WHERE shot_id = %s", (whole,))
        self.assertGreater(corr, 0.9)
        self.assertEqual(lag, 0.0)
        self.assertGreater(pixels, 1000)
        self.assertAlmostEqual(frames, 60, delta=2)
        self.assertEqual(coordination.measure("show"), [])
        self.assertEqual(coordination.measure("show", redo=True, limit=1), [("show.mp4", 1, 1)])
        scores.run("demo")
        ((value, sense),) = self.query(
            "SELECT value, modality FROM media_video.scores WHERE name = 'coordination' AND subject_id = %s", (whole,))
        self.assertEqual((value, sense), (corr, "picture"))

    def test_one_stretch_of_a_file_is_read_and_no_more(self):
        pictures = list(ffmpeg.frames(self.footage / "show.mp4", fps=10, size=(16, 9), start_s=2.0, seconds=1.5))
        self.assertAlmostEqual(len(pictures), 15, delta=1)
        self.assertIsNone(coordination.watch(self.footage / "show.mp4", 5.8, 0.2))

    def test_each_line_is_measured_once(self):
        lines = write_lines(self.talk, [(2.0, "ba ko mi ru te la no si"), (12.0, "du fe ga po vi zu he"),
                                        (39.99, "ba")])
        self.assertEqual(audible.scan("demo"), [("talk.wav", 2)])
        ((snr, noise, clipped),) = self.query(
            "SELECT snr_db, noise_db, clipped_ratio FROM media_video.audio_quality WHERE segment_id = %s", (lines[0],))
        self.assertGreater(snr, 20.0)
        self.assertLess(noise, -30.0)
        self.assertEqual(clipped, 0.0)
        self.assertEqual(audible.verdicts("demo"), [("clean", 2)])
        self.assertEqual(audible.verdicts("somewhere-else"), [])
        self.assertEqual(audible.scan("somewhere-else", redo=True), [])
        self.assertEqual(audible.scan("demo", "talk", redo=True), [("talk.wav", 2)])

    def test_a_recording_is_listened_to_once_and_what_a_person_said_is_kept(self):
        self.assertEqual(laughter.scan("demo"), [("talk.wav", 1)])      # the film is too short to judge
        ((laugh_id, name, start, end, sure, said),) = laughter.found("demo")
        self.assertEqual((name, said), ("talk.wav", None))
        self.assertGreaterEqual(start, 19.0)
        self.assertLessEqual(end, 24.0)
        self.assertEqual(laughter.scan("demo"), [])
        self.assertEqual(self.query("SELECT meta -> 'laughter' ->> 'found' FROM media.assets WHERE id = %s",
                                    (self.talk,)), [("1",)])

        self.assertEqual(laughter.rule(str(laugh_id)[:8], True), ("talk.wav", start, end))
        self.assertEqual(laughter.found("demo")[0][5], True)
        # Listening again replaces what nobody has ruled on, and only that.
        self.query("INSERT INTO media_video.laughter (asset_id, start_s, end_s, confidence) "
                   "VALUES (%s, 3.0, 5.0, 0.9) RETURNING id", (self.talk,))
        self.assertEqual(laughter.scan("demo", redo=True), [("talk.wav", 1)])
        self.assertEqual([(found[2], found[5]) for found in laughter.found("demo")], [(start, True)])
        laughter.rule(str(laugh_id), False)
        self.assertEqual(laughter.found("demo"), [])
        self.assertEqual(laughter.found("somewhere-else"), [])

    def test_a_laugh_is_named_by_the_start_of_its_id(self):
        for prefix, why in (("zzzz", "is not an id"), ("ab", "is not an id"), ("0000000000", "no laugh's id starts")):
            with self.assertRaisesRegex(laughter.LaughterError, why):
                laughter.rule(prefix, True)
        self.query("""INSERT INTO media_video.laughter (id, asset_id, start_s, end_s, confidence)
                      VALUES ('abcd0000-0000-4000-8000-000000000001', %s, 1, 2, 0.8),
                             ('abcd0000-0000-4000-8000-000000000002', %s, 5, 6, 0.8) RETURNING id""",
                   (self.talk, self.talk))
        with self.assertRaisesRegex(laughter.LaughterError, "more than one"):
            laughter.rule("abcd", True)
        self.assertEqual(laughter.rule("abcd0000-0000-4000-8000-000000000002", True), ("talk.wav", 5.0, 6.0))

    def test_the_commands_say_what_they_measured(self):
        self.assertEqual(run("loudness", "--match", "show")[:2], (0, "      3 shots  show.mp4\n3 shots measured\n"))
        self.assertEqual(run("loudness", "--match", "show")[:2], (0, "0 shots measured\n"))
        self.assertEqual(run("coordination", "--match", "show")[:2],
                         (0, "      3 shots, 3 with something lit  show.mp4\n3 shots measured\n"))
        write_lines(self.talk, [(2.0, "ba ko mi ru te la no si")])
        self.assertEqual(run("audible", "--project", "demo")[:2],
                         (0, "      1 lines  talk.wav\n1 lines measured\n      1 clean\n"))
        status, out, _ = run("laughter", "--project", "demo")
        first, second, listed = out.splitlines()
        self.assertEqual((status, first, second), (0, "      1 found  talk.wav", "1 recordings listened to"))
        self.assertRegex(listed, r"^  [0-9a-f]{8}  0\.\d\d  0:2\d\.\d-0:2\d\.\d  talk\.wav$")
        short = listed.split()[0]
        self.assertRegex(run("laughter", "--yes", short)[1], r"^talk\.wav 0:2\d\.\d-0:2\d\.\d: laughter\n$")
        self.assertTrue(run("laughter", "--project", "demo")[1].endswith("talk.wav  yes\n"))
        self.assertRegex(run("laughter", "--no", short)[1], r": not laughter\n$")
        self.assertEqual(run("laughter", "--project", "demo")[1], "0 recordings listened to\n")
        self.assertEqual(run("laughter", "--no", "zzzz")[::2],
                         (1, "media: 'zzzz' is not an id: give at least the first 4 characters of one\n"))
        self.assertEqual(run("laughter", "--yes", short, "--no", short)[0], 2)


class MeasuredScoresTest(Scored):
    """What the measures add to the scores of the made-up interview."""

    def heard(self, asset_id, snr, clipped=0.0, high=0.13):
        self.query(
            """INSERT INTO media_video.audio_quality (segment_id, rms_db, noise_db, snr_db, clipped_ratio, hf_ratio)
               SELECT id, -20, -20 - %s::float8, %s::float8, %s::float8, %s::float8 FROM media_video.segments WHERE asset_id = %s
               ON CONFLICT (segment_id) DO UPDATE SET snr_db = EXCLUDED.snr_db, clipped_ratio = EXCLUDED.clipped_ratio,
                                                      hf_ratio = EXCLUDED.hf_ratio
               RETURNING segment_id""", (snr, snr, clipped, high, asset_id))

    def test_a_passage_sounds_as_good_as_its_cleanest_recording(self):
        self.heard(self.camera, 9.0, clipped=0.004, high=0.5)
        self.heard(self.collar, 15.0, clipped=0.002, high=0.24)
        written = scores.run("made-up")
        self.assertEqual((written["audio_snr"], written["undistorted"], written["spectral_balance"]), (2, 2, 2))
        value, raw, tier, sense, producer = self.score(self.first, "audio_snr")
        self.assertAlmostEqual(value, 0.5)
        self.assertAlmostEqual(raw, 15.0)
        self.assertEqual((tier, sense, producer), (2, "audio", "audible"))
        self.assertAlmostEqual(self.score(self.first, "undistorted")[0], 0.6)
        self.assertAlmostEqual(self.score(self.first, "spectral_balance")[0], 0.5)

    def test_what_is_past_either_end_of_the_scale_is_held_there(self):
        self.heard(self.collar, 40.0, clipped=0.2, high=0.9)
        scores.run()
        self.assertEqual([self.score(self.second, name)[0] for name in ("audio_snr", "undistorted", "spectral_balance")],
                         [1.0, 0.0, 0.0])

    def test_a_measurement_that_failed_is_not_the_cleanest(self):
        self.heard(self.camera, 12.0)
        self.heard(self.collar, 80.0)
        scores.run()
        self.assertAlmostEqual(self.score(self.first, "audio_snr")[1], 12.0)
        self.query("DELETE FROM media_video.audio_quality q USING media_video.segments s "
                   "WHERE s.id = q.segment_id AND s.asset_id = %s RETURNING q.segment_id", (self.camera,))
        self.query("DELETE FROM media_video.scores WHERE producer = 'audible' RETURNING id")
        self.assertNotIn("audio_snr", scores.run())

    def test_nothing_measured_is_no_score(self):
        scores.run()
        for name in ("audio_snr", "undistorted", "spectral_balance", "laughter"):
            self.assertIsNone(self.score(self.first, name), name)

    def laugh(self, asset_id, start, end, sure=0.8):
        return self.query("INSERT INTO media_video.laughter (asset_id, start_s, end_s, confidence) "
                          "VALUES (%s, %s, %s, %s) RETURNING id", (asset_id, start, end, sure))[0][0]

    def test_a_laugh_just_after_a_passage_is_the_passages(self):
        # The first passage ends at 29.85 in the take's time. The collar
        # microphone started five seconds late, so this laugh is at 31 to 33.
        laugh = self.laugh(self.collar, 26.0, 28.0)
        written = scores.run()
        self.assertEqual(written["laughter"], 2)
        self.assertEqual(self.score(self.first, "laughter"), (0.8, 1.0, 2, "audio", "laughter"))
        # Listened to, and nobody laughed: a finding, not a gap.
        self.assertEqual(self.score(self.second, "laughter")[:2], (0.0, 0.0))

        self.query("UPDATE media_video.laughter SET confirmed = true WHERE id = %s RETURNING id", (laugh,))
        scores.run()
        self.assertEqual(self.score(self.first, "laughter")[:2], (1.0, 1.0))
        self.query("UPDATE media_video.laughter SET confirmed = false WHERE id = %s RETURNING id", (laugh,))
        scores.run()
        self.assertEqual(self.score(self.first, "laughter")[:2], (0.0, 0.0))

    def test_a_laugh_long_after_a_passage_is_not_its(self):
        self.laugh(self.camera, 36.5, 38.0)
        self.laugh(self.camera, 58.5, 60.0, sure=0.9)
        self.laugh(self.camera, 59.0, 61.0, sure=0.75)
        scores.run()
        self.assertEqual(self.score(self.first, "laughter")[:2], (0.0, 0.0))
        self.assertEqual(self.score(self.second, "laughter")[:2], (0.9, 2.0))

    def test_a_recording_listened_to_with_nothing_found_still_scores(self):
        self.query("""UPDATE media.assets SET meta = meta || '{"laughter": {"found": 0}}' WHERE id = %s RETURNING id""",
                   (self.camera,))
        scores.run()
        self.assertEqual(self.score(self.first, "laughter")[:2], (0.0, 0.0))

    def test_a_shot_is_scored_by_ear_and_by_what_moves_together(self):
        near, wide, other = self.footage()
        self.assertEqual(scores.ranked("laser-reel", project="made-up").entries, [])
        self.query("UPDATE media_video.shots SET lift_db = 3.0 WHERE id = %s RETURNING id", (near,))
        self.query("UPDATE media_video.shots SET lift_db = -2.0 WHERE id = %s RETURNING id", (wide,))
        self.query("INSERT INTO media_video.coordination (shot_id, corr, n_frames) "
                   "VALUES (%s, 0.7, 40), (%s, NULL, 40), (%s, -0.2, 40) RETURNING shot_id", (near, wide, other))
        written = scores.run()
        self.assertEqual((written["energy_lift"], written["coordination"]), (2, 2))
        self.assertEqual(self.score(near, "energy_lift"), (0.5, 3.0, 2, "audio", "loudness"))
        self.assertEqual(self.score(wide, "energy_lift")[:2], (0.0, -2.0))
        self.assertIsNone(self.score(other, "energy_lift"))
        self.assertEqual(self.score(near, "coordination"), (0.7, 0.7, 2, "picture", "coordination"))
        # Nothing was lit in the wide shot, which is no answer and not a nought.
        self.assertIsNone(self.score(wide, "coordination"))
        self.assertEqual(self.score(other, "coordination")[:2], (0.0, -0.2))
        # Two senses now speak for the shot, which is what the recipe asks for.
        self.assertEqual(len(scores.ranked("laser-reel", project="made-up").entries), 2)
