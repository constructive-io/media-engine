"""The measurements takes rest on, each tried with made-up speech and sound."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from constructive_media import ffmpeg
from constructive_media.takes import align, check, clock, loudness
from constructive_media.takes.words import normalise, telling

from .support import heard, make_talk, needs_ffmpeg, needs_numpy

UTC = timezone.utc


class WordsTest(unittest.TestCase):
    def test_a_word_is_compared_as_letters_and_apostrophes_in_any_alphabet(self):
        self.assertEqual(normalise(" Don't, "), "don't")
        self.assertEqual(normalise("Café."), "café")
        self.assertEqual(normalise("ÜBER"), normalise("über"))
        self.assertEqual(normalise("— 42 —"), "")

    def test_short_and_common_words_carry_no_evidence(self):
        said = [("the", 0.0), ("harbour", 0.4), ("is", 0.8), ("ox", 1.0), ("would", 1.2), ("fog", 1.5)]
        self.assertEqual(telling(said), [("harbour", 0.4), ("fog", 1.5)])
        self.assertEqual(telling(said, 4), [("harbour", 0.4)])


class AlignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.talk = make_talk(900)
        # The microphone rolled from the start; the camera started 100 s in.
        cls.microphone = heard(cls.talk, 0, 900, seed=3)
        cls.camera = heard(cls.talk, 100, 400, seed=4)

    def test_the_offset_is_what_to_add_to_the_others_time_to_reach_the_references(self):
        found = align.by_words(self.camera, self.microphone)
        self.assertEqual(found.offset_s, -100.0)
        self.assertGreater(found.votes, 100)
        self.assertEqual(align.by_words(self.microphone, self.camera).offset_s, 100.0)

    def test_recordings_of_different_things_do_not_align(self):
        elsewhere = heard(make_talk(900, seed=50), 0, 900)
        self.assertIsNone(align.by_words(self.camera, elsewhere))
        self.assertIsNone(align.align(self.camera, elsewhere))

    def test_too_little_said_is_no_answer(self):
        self.assertIsNone(align.by_words(self.camera[:15], self.microphone))
        self.assertIsNone(align.by_words([], []))

    def test_a_refrain_does_not_outvote_the_recording(self):
        refrain = [("bakoba", 0.0), ("mirute", 0.5), ("lanosi", 1.0), ("dufega", 1.5)]

        def with_refrain(words, at):
            return sorted(words + [(w, t + start) for start in at for w, t in refrain], key=lambda x: x[1])

        # The same four words, six times in each, never at the true offset.
        camera = with_refrain(self.camera, (5, 55, 105, 155, 205, 255))
        microphone = with_refrain(self.microphone, (30, 230, 430, 630, 730, 830))
        self.assertEqual(align.by_words(camera, microphone).offset_s, -100.0)

    def test_only_phrases_said_once_are_anchors(self):
        said = [(word, float(n)) for n, word in enumerate("a b c d e f a b c d e g".split())]
        found = align.anchors(said)
        self.assertNotIn(tuple("abcde"), found)
        self.assertEqual(found[tuple("bcdef")], 1.0)
        self.assertEqual(found[tuple("bcdeg")], 7.0)

    def test_phrases_measure_what_the_vote_only_finds(self):
        offset, spread, phrases = align.by_phrases(self.camera, self.microphone, -100.0)
        self.assertAlmostEqual(offset, -100.0, delta=0.03)
        self.assertLess(spread, 0.05)
        self.assertGreater(phrases, 50)

    def test_phrases_sharpen_an_answer_and_do_not_look_for_one(self):
        self.assertIsNone(align.by_phrases(self.camera, self.microphone, -80.0))
        self.assertIsNone(align.by_phrases(self.camera[:12], self.microphone, -100.0))

    def test_an_alignment_says_how_it_was_reached(self):
        found = align.align(self.camera, self.microphone)
        self.assertEqual(found.method, "phrases")
        self.assertAlmostEqual(found.offset_s, -100.0, delta=0.03)
        # A recording that misses half of what is said keeps the words and
        # loses the phrases.
        patchy = align.align(heard(self.talk, 100, 400, miss=0.6, seed=5), heard(self.talk, 0, 900, miss=0.6, seed=6))
        self.assertEqual((patchy.method, patchy.offset_s, patchy.spread_s), ("words", -100.0, None))


class CheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.talk = make_talk(900)
        cls.microphone = heard(cls.talk, 0, 900, seed=3)
        cls.camera = heard(cls.talk, 100, 400, seed=4)

    def judge(self, offset, **options):
        return check.check(self.camera, self.microphone, offset, other_duration_s=900, **options)

    def test_a_right_offset_agrees_and_peaks_where_it_points(self):
        verdict = self.judge(-100.0)
        self.assertEqual((verdict.state, verdict.residual_s, verdict.beyond_search), (check.OK, 0.0, False))
        self.assertGreater(verdict.agreement, 0.6)
        self.assertEqual(verdict.windows, 6)
        self.assertTrue(verdict.decided)

    def test_a_wrong_offset_is_caught_with_roughly_how_to_correct_it(self):
        verdict = self.judge(-90.0)
        self.assertEqual(verdict.state, check.MISMATCH)
        self.assertAlmostEqual(verdict.residual_s, -10.0, delta=1.0)
        self.assertAlmostEqual(self.judge(-112.0).residual_s, 12.0, delta=1.0)

    def test_the_right_offset_stored_the_wrong_way_round_is_caught(self):
        self.assertNotEqual(self.judge(100.0).state, check.OK)

    def test_an_error_at_the_edge_of_the_search_is_not_reported_as_a_measurement(self):
        verdict = self.judge(-50.0)
        self.assertEqual((verdict.state, verdict.residual_s, verdict.beyond_search), (check.MISMATCH, None, True))

    def test_nothing_in_common_within_reach_is_not_a_verdict(self):
        far = self.judge(-600.0)
        self.assertEqual(far.state, check.NOTHING)
        self.assertFalse(far.decided)
        self.assertEqual(check.check(self.camera, [], -100.0).state, check.NOTHING)
        self.assertEqual(check.check([], self.microphone, -100.0).state, check.NOTHING)

    def test_one_window_on_a_recording_that_covers_little_is_too_little(self):
        clip = heard(self.talk, 110, 25, seed=7)
        verdict = check.check(self.camera, clip, -10.0, other_duration_s=25)
        self.assertEqual((verdict.state, verdict.windows), (check.TOO_LITTLE, 1))

    def test_asked_from_the_side_that_says_less_a_short_clip_is_decided(self):
        clip = heard(self.talk, 310, 60, seed=7)
        verdict = check.either(self.microphone, clip, 310.0, one_duration_s=900, other_duration_s=60)
        self.assertEqual((verdict.state, verdict.residual_s), (check.OK, 0.0))
        self.assertGreaterEqual(verdict.windows, 4)

    def test_the_correction_means_the_same_from_either_side(self):
        clip = heard(self.talk, 310, 120, seed=7)
        out = check.either(self.microphone, clip, 318.0, one_duration_s=900, other_duration_s=120)
        self.assertEqual(out.state, check.MISMATCH)
        self.assertAlmostEqual(out.residual_s, -8.0, delta=1.0)
        back = check.either(clip, self.microphone, -318.0, one_duration_s=120, other_duration_s=900)
        self.assertAlmostEqual(back.residual_s, 8.0, delta=1.0)


class ClockTest(unittest.TestCase):
    def test_a_time_is_read_from_a_file_name(self):
        plus_two = clock.zone(2)
        expected = datetime(2026, 1, 2, 3, 4, 5, tzinfo=plus_two)
        for name in ("REC_20260102_030405_a.wav", "20260102030405.mov", "clip 2026-01-02 03.04.05.mp4",
                     "2026-01-02T03-04-05.wav", "cam-b_20260102-030405.mov"):
            self.assertEqual(clock.from_name(name, plus_two), expected, name)
        self.assertEqual(expected.astimezone(UTC).hour, 1)

    def test_a_name_with_no_zone_is_read_in_this_computers(self):
        found = clock.from_name("REC_20260102_030405.wav")
        self.assertEqual(found, datetime(2026, 1, 2, 3, 4, 5).astimezone())

    def test_what_is_not_a_believable_time_is_not_read_as_one(self):
        for name in ("IMG_1234.mov", "take_20261402_030405.wav", "take_20260102_250000.wav",
                     "REC_19700101_000000.wav", "12345620260102030405.wav", "20260102.wav"):
            self.assertIsNone(clock.from_name(name), name)

    def test_recordings_that_overlap_are_one_group_even_through_another(self):
        spans = [("mic", 0, 1800), ("cam-1", 100, 500), ("cam-2", 900, 1500),
                 ("later", 1790, 3000), ("alone", 5000, 6000)]
        self.assertEqual(clock.overlapping(spans), [["mic", "cam-1", "cam-2"]])
        self.assertEqual(clock.overlapping(spans, 5), [["mic", "cam-1", "cam-2", "later"]])
        self.assertEqual(clock.overlapping([]), [])

    def test_take_time_zero_needs_two_clocks_that_agree(self):
        noon = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
        camera, microphone = (noon, 0.0), (noon - timedelta(seconds=98), -100.0)
        started, spread = clock.start([camera, microphone, (noon + timedelta(seconds=31), 30.0)])
        self.assertEqual((started, spread), (noon + timedelta(seconds=1), 2.0))
        self.assertIsNone(clock.start([camera]))
        self.assertIsNone(clock.start([camera, (noon, -100.0)]))


@needs_numpy
class LoudnessTest(unittest.TestCase):
    def room(self, seed):
        import numpy as np

        chance = np.random.default_rng(seed)
        sound = np.where(chance.random(12000) > 0.97, chance.random(12000) * 8, 0.0)
        return sound, chance

    def test_one_recording_is_found_inside_another(self):
        sound, chance = self.room(1)
        long = sound[1000:9000] + chance.normal(0, 0.3, 8000)
        short = sound[3000:7000] * 0.4 + chance.normal(0, 0.3, 4000)
        found = loudness.offset(long, short)
        self.assertEqual(found.offset_s, 20.0)
        self.assertTrue(found.sure)
        self.assertEqual(loudness.offset(short, long).offset_s, -20.0)

    def test_neither_recording_need_contain_the_other(self):
        sound, chance = self.room(2)
        first, second = sound[:6000], sound[4000:11000] + chance.normal(0, 0.3, 7000)
        found = loudness.offset(first, second)
        self.assertEqual((found.offset_s, found.sure), (40.0, True))

    def test_unrelated_recordings_have_no_peak_worth_believing(self):
        found = loudness.offset(self.room(3)[0][:8000], self.room(4)[0][:6000])
        self.assertFalse(found.sure)

    def test_nothing_to_compare_is_no_answer(self):
        import numpy as np

        self.assertIsNone(loudness.offset(None, self.room(1)[0]))
        self.assertIsNone(loudness.offset(np.zeros(1), np.zeros(1)))

    @needs_ffmpeg
    def test_two_files_of_one_room_are_placed_by_how_they_sound(self):
        with tempfile.TemporaryDirectory() as folder:
            room, clip, silent = (Path(folder) / name for name in ("room.wav", "clip.wav", "silent.wav"))
            # Bursts of tone that come and go without a pattern.
            bursts = "0.6*sin(2*PI*330*t)*gt(sin(2*PI*0.31*t)*sin(2*PI*0.17*t+1)*sin(2*PI*1.3*t),0.2)"
            ffmpeg.run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{bursts}':d=60:s=16000", room)
            ffmpeg.run("ffmpeg", "-v", "error", "-y", "-ss", "12.5", "-t", "30", "-i", room,
                       "-f", "lavfi", "-i", "anoisesrc=d=30:c=pink:a=0.02:r=16000",
                       "-filter_complex", "[0:a]volume=0.3[quiet];[quiet][1:a]amix=inputs=2:normalize=0", clip)
            ffmpeg.run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000", "-t", "5", silent)
            found = loudness.measure(room, clip)
            self.assertAlmostEqual(found.offset_s, 12.5, delta=0.02)
            self.assertTrue(found.sure)
            self.assertIsNone(loudness.rises(silent))
            self.assertIsNone(loudness.rises(Path(folder) / "missing.wav"))


@needs_ffmpeg
class RecordingTimeAndSoundTest(unittest.TestCase):
    def test_the_time_inside_a_file_is_read_and_a_file_without_one_has_none(self):
        with tempfile.TemporaryDirectory() as folder:
            stamped, bare = Path(folder) / "stamped.mov", Path(folder) / "bare.wav"
            for path, extra in ((stamped, ("-metadata", "creation_time=2026-01-02T03:04:05Z")), (bare, ())):
                ffmpeg.run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.2", *extra, path)
            self.assertEqual(ffmpeg.created(stamped), datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
            self.assertEqual(clock.read(stamped), (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "container"))
            self.assertIsNone(ffmpeg.created(bare))
            self.assertIsNone(clock.read(bare))
            self.assertIsNone(ffmpeg.created(Path(folder) / "missing.mov"))

    def test_sound_is_read_as_one_channel_of_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            tone = Path(folder) / "tone.wav"
            ffmpeg.run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.5", "-ac", "2", tone)
            read = b"".join(ffmpeg.samples(tone, rate=8000, chunk=1000))
            self.assertEqual(len(read), 8000)
            self.assertEqual(b"".join(ffmpeg.samples(Path(folder) / "missing.wav", rate=8000)), b"")
