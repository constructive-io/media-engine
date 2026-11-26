"""A line said until it is said right.

No model speaks or listens here. A stand-in writes made-up talk and is told
what to get wrong; another is told what to mishear.
"""

import tempfile
import unittest
from pathlib import Path

from constructive_media.audio import check
from constructive_media.speech import GivenVoices, Voice
from constructive_media.text import TextError

from .support import HAVE_FFMPEG, HAVE_NUMPY, Listener, Speaker, needs_ffmpeg, needs_numpy

if HAVE_NUMPY:
    from constructive_media.audio import line, pcm

LINE = "The lantern under the harbour wall is lit at dusk."
ADA = Voice("ada")
MUDDLE = "ba ko mi ru te la no si du fe"


@needs_numpy
@needs_ffmpeg
class Said(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def speak(self, text=LINE, voice=ADA, *, wrong=None, hears=None, **how):
        self.speaker = Speaker(wrong)
        self.listener = Listener(self.speaker, hears)
        return line.speak(text, voice, self.folder, synthesizer=self.speaker, transcriber=self.listener,
                          **{"seed": 500, **how})

    def seeds(self):
        return [seed for _, _, seed in self.speaker.asked]


class Speaking(Said):
    def test_a_line_that_comes_out_right_is_said_once(self):
        take = self.speak()
        self.assertEqual((take.status, take.attempts, take.seed, take.spoken), (line.CLEAN, 1, 500, LINE))
        self.assertTrue(take.ok)
        self.assertEqual(take.verdict.reason, check.CLEAN)
        self.assertEqual(take.path, self.folder / "line_a1.wav")
        self.assertTrue(take.path.is_file())
        self.assertEqual(self.speaker.asked, [(LINE, ADA, 500)])
        self.assertIsNone(take.repeat)
        self.assertIsNone(take.tail_db)

    def test_a_voice_with_a_seed_of_its_own_is_said_with_it(self):
        take = self.speak(voice=Voice("ada", seed=31))
        self.assertEqual(take.seed, 31)

    def test_a_line_that_comes_out_wrong_is_said_again_with_another_seed(self):
        take = self.speak(hears=lambda text, seed: MUDDLE if seed == 500 else text)
        self.assertEqual((take.status, take.attempts, take.seed), (line.HEALED, 2, 757))
        self.assertTrue(take.ok)
        self.assertEqual(take.path.name, "line_a2.wav")

    def test_shouted_words_are_calmed_before_another_seed_is_tried(self):
        shouted = "This is NEVER going to work, said the keeper."
        take = self.speak(shouted, hears=lambda text, seed: MUDDLE if "NEVER" in text else text)
        self.assertEqual((take.status, take.attempts, take.seed), (line.HEALED, 2, 500))
        self.assertEqual(take.spoken, "This is Never going to work, said the keeper.")

    def test_a_line_no_seed_mends_is_kept_at_its_best_and_marked(self):
        take = self.speak(hears=lambda text, seed: "the lantern ba ko mi ru te la no si" if seed == 1014 else MUDDLE)
        self.assertEqual((take.status, take.attempts), (line.NEEDS_REVIEW, 5))
        self.assertFalse(take.ok)
        self.assertEqual(self.seeds(), [500, 757, 1014, 1271, 1528])
        # The nearest of the five, not the last.
        self.assertEqual(take.seed, 1014)
        self.assertEqual((take.verdict.reason, take.verdict.word_error), (check.GARBLED, 0.8))

    def test_how_many_times_it_is_tried_can_be_set(self):
        take = self.speak(hears=lambda text, seed: MUDDLE, attempts=2)
        self.assertEqual((take.attempts, self.seeds()), (2, [500, 757]))

    def test_a_short_line_gets_more_seeds_and_is_never_reworded(self):
        asked = []
        take = self.speak("Lit at dusk.", hears=lambda text, seed: text if seed == 500 + 3000 + 2 * 401 else "ba ko mi",
                          reword=lambda text: asked.append(text) or "The lamps are lit at dusk.")
        self.assertEqual((take.status, take.attempts, take.seed), (line.HEALED, 8, 4302))
        self.assertEqual(asked, [])
        self.assertEqual(take.spoken, "Lit at dusk.")

    def test_a_stutter_is_the_voice_s_doing_and_gets_more_seeds(self):
        take = self.speak(wrong=lambda text, seed: "stutter" if seed < 3500 else None,
                          reword=lambda text: self.fail("a voice is not mended by other words"))
        self.assertEqual((take.status, take.attempts, take.seed), (line.HEALED, 6, 3500))
        self.assertIsNone(take.repeat)

    def test_a_stutter_that_never_goes_is_marked_with_where_it_was_heard(self):
        take = self.speak(wrong=lambda text, seed: "stutter")
        self.assertEqual((take.status, take.attempts, take.verdict.reason), (line.NEEDS_REVIEW, 11, line.STUTTER))
        self.assertEqual(self.seeds()[5:], [3500, 3901, 4302, 4703, 5104, 5505])
        self.assertAlmostEqual(take.repeat.gap_s, 0.24, delta=0.011)
        self.assertAlmostEqual(take.repeat.first_s, 1.0, delta=0.05)
        # The words were right all along.
        self.assertEqual(take.verdict.word_error, 0.0)

    def test_stutters_are_not_listened_for_when_that_is_turned_off(self):
        take = self.speak(wrong=lambda text, seed: "stutter", repeats=False)
        self.assertEqual((take.status, take.attempts), (line.CLEAN, 1))

    def test_a_line_that_stops_in_the_middle_of_a_sound_was_cut_off(self):
        take = self.speak(wrong=lambda text, seed: "cut", more_seeds=1)
        self.assertEqual((take.status, take.attempts, take.verdict.reason), (line.NEEDS_REVIEW, 6, line.CUT_OFF))
        self.assertGreater(take.tail_db, -20)

    def test_the_voice_s_own_recording_leaking_into_the_line_gets_more_seeds(self):
        voice = Voice("ada", reference_text="The tide comes in at seven.")
        take = self.speak("Lanterns are lit along the wall tonight.", voice,
                          hears=lambda text, seed: text if seed == 3500 else "at seven " + text)
        self.assertEqual((take.status, take.attempts, take.seed), (line.HEALED, 6, 3500))

    def test_a_long_line_is_reworded_only_when_that_is_asked_for(self):
        other = "At dusk, the lantern below the harbour wall is lit."
        hears = lambda text, seed: text if text == other else MUDDLE  # noqa: E731
        left = self.speak(hears=hears)
        self.assertEqual((left.status, left.attempts), (line.NEEDS_REVIEW, 5))

        asked = []
        take = self.speak(hears=hears, reword=lambda text: asked.append(text) or other)
        self.assertEqual((take.status, take.attempts, take.seed), (line.REWORDED, 6, 7500))
        self.assertTrue(take.ok)
        self.assertEqual((take.spoken, asked), (other, [LINE]))

    def test_each_rewording_is_made_from_the_one_before_and_tried_twice(self):
        asked = []
        take = self.speak(hears=lambda text, seed: MUDDLE, attempts=1, rewordings=2,
                          reword=lambda text: asked.append(text) or text + " Again.")
        self.assertEqual(asked, [LINE, LINE + " Again."])
        self.assertEqual(self.seeds(), [500, 7500, 7631, 7631, 7762])
        self.assertEqual((take.status, take.attempts), (line.NEEDS_REVIEW, 5))

    def test_a_model_that_fails_is_a_failed_take_and_not_the_end(self):
        take = self.speak(wrong=lambda text, seed: RuntimeError("the made-up model\n fell over") if seed == 500 else None)
        self.assertEqual((take.status, take.attempts), (line.HEALED, 2))

        take = self.speak(wrong=lambda text, seed: RuntimeError("the made-up model\n fell over"),
                          reword=lambda text: self.fail("nothing could be said: the words are not at fault"))
        self.assertEqual((take.status, take.attempts, take.verdict.reason), (line.NEEDS_REVIEW, 5, line.ERROR))
        self.assertIsNone(take.path)
        self.assertEqual(take.verdict.heard, "the made-up model fell over")
        self.assertEqual(take.verdict.word_error, 1.0)

    def test_a_file_with_nothing_in_it_is_a_failed_take(self):
        take = self.speak(wrong=lambda text, seed: "empty")
        self.assertEqual((take.status, take.verdict.reason, take.verdict.heard),
                         (line.NEEDS_REVIEW, line.ERROR, "nothing was said"))
        self.assertIsNone(take.path)

    def test_a_listener_that_fails_is_a_failed_take_whose_sound_is_kept(self):
        def deaf(text, seed):
            raise ValueError

        take = self.speak(hears=deaf, attempts=2)
        self.assertEqual((take.status, take.verdict.reason, take.verdict.heard),
                         (line.NEEDS_REVIEW, line.ERROR, "ValueError"))
        self.assertTrue(take.path.is_file())

    def test_the_start_of_a_take_is_cleaned_once(self):
        take = self.speak()
        before = take.path.read_bytes()
        self.assertEqual(pcm.rate(take.path), 24_000)
        self.assertEqual(self.listener.listened, [str(take.path)])
        # Listened to after it was cleaned, and not touched since.
        self.assertEqual(before, take.path.read_bytes())
        self.assertAlmostEqual(abs(float(pcm.read(take.path, stereo=False)[0])), 0.0, delta=0.01)


class Reworder:
    name, model = "stand-in", "made-up-writer"

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def write(self, prompt, *, limit=200, as_json=False):
        self.asked.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@unittest.skipUnless(HAVE_NUMPY and HAVE_FFMPEG, "numpy or ffmpeg is not installed")
class Rewording(unittest.TestCase):
    def test_the_model_is_asked_to_keep_the_meaning_and_its_answer_is_tidied(self):
        writer = Reworder('Sure.\n\nRephrased: "At dusk the lantern is lit."\n')
        self.assertEqual(line.rewording(writer)("The lantern is lit at dusk."), "At dusk the lantern is lit.")
        (prompt,) = writer.asked
        self.assertIn("Sentence: The lantern is lit at dusk.", prompt)
        self.assertIn("Do not change what it means", prompt)

    def test_a_model_with_nothing_to_say_leaves_the_line_as_it_was(self):
        for answer in ("", "  \n ", '""', TextError("the text model did not answer")):
            with self.subTest(answer=answer):
                self.assertEqual(line.rewording(Reworder(answer))("Lit at dusk."), "Lit at dusk.")


VOICES = GivenVoices([
    Voice("ada", engine="stand-in", profile="ada-plain"),
    Voice("ada", "whisper", engine="stand-in", profile="ada-hushed"),
    Voice("ada", "warm", engine="other", profile="ada-warm", seed=90),
])


def text(words, **overrides):
    return {"type": "text", "text": words, "overrides": overrides}


class Performing(Said):
    def perform(self, segments, *, wrong=None, hears=None, **how):
        self.speaker = Speaker(wrong)
        self.listener = Listener(self.speaker, hears)
        self.engines = []
        return line.perform(segments, self.folder, persona="ada", voices=VOICES,
                            synthesizer=lambda engine: self.engines.append(engine) or self.speaker,
                            transcriber=self.listener, **{"seed": 500, **how})

    def test_a_line_of_one_piece_is_that_piece(self):
        said = self.perform([text(LINE)], name="line_003")
        self.assertEqual((said.text, said.voice.profile, said.effects), (LINE, "ada-plain", ()))
        self.assertEqual((said.take.status, said.take.seed, said.take.path.name), (line.CLEAN, 500, "line_003_a1.wav"))
        self.assertEqual(self.engines, ["stand-in"])

    def test_the_line_s_emotion_chooses_the_voice_and_its_engine(self):
        said = self.perform([text(LINE)], emotion="warm")
        self.assertEqual((said.voice.profile, said.take.seed, self.engines), ("ada-warm", 90, ["other"]))
        # An emotion the persona has no voice for is said in the neutral one.
        self.assertEqual(self.perform([text(LINE)], emotion="playful").voice.profile, "ada-plain")

    def test_a_seed_on_the_line_is_the_seed_it_is_said_with(self):
        said = self.perform([text(LINE)], emotion="warm", line_seed=7)
        self.assertEqual((said.take.seed, said.voice.seed), (7, 7))

    def test_pieces_are_said_one_by_one_and_joined(self):
        said = self.perform([
            text("The lantern is lit."), text("Come closer now.", emotion="whisper"), {"type": "break", "seconds": 0.5},
            text("And listen."), {"type": "sfx", "sound": "DING", "overrides": {}}, text("To the harbour wall tonight."),
        ], name="line_004")
        self.assertEqual([(words, voice.profile, seed) for words, voice, seed in self.speaker.asked], [
            ("The lantern is lit.", "ada-plain", 500), ("Come closer now.", "ada-hushed", 507),
            ("And listen.", "ada-plain", 514), ("To the harbour wall tonight.", "ada-plain", 521),
        ])
        self.assertEqual(said.text, "The lantern is lit. Come closer now. And listen. To the harbour wall tonight.")
        self.assertEqual((said.take.status, said.take.attempts, said.take.path.name), (line.CLEAN, 4, "line_004.wav"))
        self.assertEqual(said.take.spoken, said.text)
        pieces = sum(pcm.seconds(self.folder / f"line_004_s{n}_a1.wav") for n in range(4))
        # Three joins of thirty thousandths of a second are overlaps; the break is not joined to anything.
        self.assertAlmostEqual(pcm.seconds(said.take.path), pieces + 0.5 - 0.03 * 2, delta=0.02)

    def test_the_whole_is_as_good_as_its_worst_piece(self):
        said = self.perform(
            [text("The lantern is lit."), text("Come closer now and listen to me."), text("The tide is in tonight.")],
            hears=lambda words, seed: MUDDLE if words.startswith("Come") or (words.startswith("The tide") and seed == 514)
            else words,
        )
        self.assertEqual((said.take.status, said.take.attempts), (line.NEEDS_REVIEW, 1 + 5 + 2))
        self.assertFalse(said.take.ok)
        self.assertEqual(said.take.verdict.reason, check.GARBLED)
        self.assertTrue(said.take.path.is_file())

    def test_a_piece_has_effects_of_its_own(self):
        plain = self.perform([text("The lantern is lit."), text("Come closer now.")], name="plain")
        said = self.perform([text("The lantern is lit."), text("Come closer now.", gain=-12.0, emotion="whisper")],
                            name="quieter")
        self.assertEqual(said.effects, ({"gain": -12.0},))
        self.assertEqual(plain.effects, ())
        loud = pcm.read(plain.take.path, stereo=False)
        quiet = pcm.read(said.take.path, stereo=False)
        self.assertAlmostEqual(float(abs(quiet[:12_000]).max()), float(abs(loud[:12_000]).max()), delta=0.01)
        self.assertLess(float(abs(quiet[-24_000:]).max()), 0.4 * float(abs(loud[-24_000:]).max()))

    def test_a_piece_s_own_seed_is_the_one_it_is_said_with(self):
        self.perform([text("The lantern is lit.", seed=11), text("Come closer now.")], line_seed=3)
        self.assertEqual([seed for _, _, seed in self.speaker.asked], [11, 3])

    def test_a_line_with_nothing_to_say_cannot_be_said(self):
        from constructive_media.speech import SpeechError

        with self.assertRaises(SpeechError):
            self.perform([{"type": "break", "seconds": 0.5}, {"type": "sfx", "sound": "DING", "overrides": {}}])


if __name__ == "__main__":
    unittest.main()
