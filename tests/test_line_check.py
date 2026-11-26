"""The check on a rendered line: what was heard against what was meant, and the sound itself.

The words are made up, and every sound is made on the spot.
"""

import unittest

from constructive_media.audio import check

from .support import HAVE_NUMPY, needs_numpy

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import listen

MEANT = "the lantern under the harbour wall is lit at dusk"


class Words(unittest.TestCase):
    def test_words_are_compared_in_lower_case_without_punctuation(self):
        self.assertEqual(check.words("The lantern's lit -- at DUSK, 7 times!"),
                         ["the", "lanterns", "lit", "at", "dusk", "7", "times"])
        self.assertEqual(check.words(None), [])

    def test_a_line_heard_as_it_was_meant_has_no_error(self):
        heard = "The lantern under the harbour wall is lit at dusk."
        self.assertEqual(check.word_error(MEANT, heard), 0.0)
        self.assertEqual(check.similarity(MEANT, heard), 1.0)

    def test_each_wrong_missing_or_extra_word_counts_once(self):
        self.assertAlmostEqual(check.word_error(MEANT, "the lantern under the harbour hall is lit at dusk"), 0.1)
        self.assertAlmostEqual(check.word_error(MEANT, "the lantern under the wall is lit at dusk"), 0.1)
        self.assertAlmostEqual(check.word_error(MEANT, "the old lantern under the harbour wall is lit at dusk"), 0.1)

    def test_the_error_is_never_more_than_one(self):
        self.assertEqual(check.word_error("lit at dusk", "ba ko mi ru te la no si du fe"), 1.0)

    def test_nothing_meant_is_nothing_wrong(self):
        self.assertEqual(check.word_error("", "anything at all"), 0.0)
        self.assertEqual(check.similarity("...", "anything at all"), 1.0)

    def test_words_made_up_after_the_line_is_over_are_not_held_against_it(self):
        heard = "lit at dusk" + " thank you" * 20
        self.assertLess(check.word_error("lit at dusk", heard), 1.01)
        # Three words meant: six are listened to, however many more were written.
        self.assertEqual(check.word_error("lit at dusk", heard), check.word_error("lit at dusk", "lit at dusk thank you thank"))
        self.assertGreater(check.similarity("lit at dusk", heard), 0.55)

    def test_a_word_heard_slightly_wrong_is_nearly_right_by_its_letters(self):
        heard = "the lanturn under the harbor wall is lit at dusk"
        self.assertAlmostEqual(check.word_error(MEANT, heard), 0.2)
        self.assertGreater(check.similarity(MEANT, heard), 0.95)


class Verdicts(unittest.TestCase):
    def test_a_line_heard_as_meant_is_clean(self):
        verdict = check.check(MEANT, "The lantern under the harbour wall is lit at dusk.")
        self.assertTrue(verdict.ok)
        self.assertEqual((verdict.reason, verdict.word_error, verdict.similarity, verdict.bled),
                         (check.CLEAN, 0.0, 1.0, None))

    def test_a_line_heard_as_something_else_is_garbled(self):
        verdict = check.check(MEANT, "zu zu zu po vi ga")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, check.GARBLED)
        self.assertEqual(verdict.heard, "zu zu zu po vi ga")

    def test_wrong_words_alone_or_unlike_letters_alone_are_not_a_garble(self):
        # A short line with a made-up tail: bad by its words, good by its letters.
        tail = check.check("lit at dusk", "lit at dusk and and and")
        self.assertGreater(tail.word_error, check.MOST_WORD_ERROR)
        self.assertTrue(tail.ok)
        # Every word a little off: bad by its words, close by its letters.
        close = check.check("lantern harbour dusk", "lanturn harbor dust")
        self.assertEqual(close.word_error, 1.0)
        self.assertTrue(close.ok)

    def test_the_limits_can_be_moved(self):
        heard = "the lantern under a harbour hall was lit at dawn"
        self.assertTrue(check.check(MEANT, heard).ok)
        self.assertFalse(check.check(MEANT, heard, most_word_error=0.3, least_similarity=0.99).ok)

    def test_a_line_that_begins_with_the_end_of_the_reference_fails(self):
        reference = "and that is how the tide comes round the breakwater"
        verdict = check.check(MEANT, "Breakwater. The lantern under the harbour wall is lit at dusk.",
                              reference=reference)
        self.assertFalse(verdict.ok)
        self.assertEqual((verdict.reason, verdict.bled), (check.REFERENCE_BLEED, "breakwater"))
        self.assertLess(verdict.word_error, 0.2, "counting words would have passed it")

    def test_without_the_reference_s_words_there_is_no_such_check(self):
        self.assertTrue(check.check(MEANT, "Breakwater. The lantern under the harbour wall is lit at dusk.").ok)


class Bleed(unittest.TestCase):
    REFERENCE = "and that is how the tide comes round the breakwater"

    def bleed(self, heard, meant=MEANT, reference=None):
        return check.bleed(meant, heard, self.REFERENCE if reference is None else reference)

    def test_a_stray_word_is_matched_however_the_transcriber_heard_it(self):
        for stray in ("breakwater", "water", "breakwaters", "brakewater"):
            with self.subTest(stray=stray):
                self.assertEqual(self.bleed(f"{stray} the lantern under the harbour wall"), stray)

    def test_several_stray_words_are_reported_together(self):
        self.assertEqual(self.bleed("the breakwater lantern under the harbour wall",
                                    meant="lantern under the harbour wall"), "the breakwater")

    def test_only_the_first_three_words_heard_are_looked_at(self):
        self.assertIsNone(self.bleed("the breakwater the lantern under the harbour wall",
                                     meant="lantern under the harbour wall"))

    def test_a_short_word_must_be_the_reference_s_last_word(self):
        self.assertIsNone(self.bleed("the lit at dusk", meant="lit at dusk"))
        self.assertEqual(self.bleed("sea lit at dusk", meant="lit at dusk", reference="down to the sea"), "sea")
        self.assertIsNone(self.bleed("the lit at dusk", meant="lit at dusk", reference="down to the sea"))

    def test_a_word_before_the_line_that_is_not_from_the_reference_is_not_a_bleed(self):
        self.assertIsNone(self.bleed("well the lantern under the harbour wall", meant="lantern under the harbour wall"))
        self.assertIsNone(self.bleed("zzzz lantern under the harbour wall", meant="lantern under the harbour wall"))

    def test_a_line_that_never_begins_is_not_a_bleed(self):
        self.assertIsNone(self.bleed("breakwater water breakwater zu zu zu"))

    def test_a_line_that_begins_as_meant_has_nothing_before_it(self):
        self.assertIsNone(self.bleed("the lantern under the breakwater"))

    def test_nothing_to_compare_is_no_bleed(self):
        self.assertIsNone(self.bleed("breakwater the lantern", reference=""))
        self.assertIsNone(self.bleed("", meant=MEANT))
        self.assertIsNone(self.bleed("breakwater", meant="..."))


class Calming(unittest.TestCase):
    def test_a_shouted_word_becomes_a_capitalised_one(self):
        self.assertEqual(check.calm("That is the FOUNDATION of it."), ("That is the Foundation of it.", True))

    def test_a_run_of_shouted_words_is_calmed_however_short_they_are(self):
        self.assertEqual(check.calm("DO IT NOW, please"), ("Do It Now, please", True))

    def test_a_short_word_in_capitals_alone_is_an_abbreviation_and_is_left(self):
        self.assertEqual(check.calm("Send it to the UN by ten."), ("Send it to the UN by ten.", False))
        self.assertEqual(check.calm("I think so."), ("I think so.", False))

    def test_words_with_apostrophes_and_everything_between_words_are_kept(self):
        self.assertEqual(check.calm("WE'RE not -- repeat, NEVER -- going!"), ("We're not -- repeat, Never -- going!", True))

    def test_text_with_nothing_shouted_comes_back_as_it_was(self):
        text = "The lantern is lit at dusk."
        self.assertEqual(check.calm(text), (text, False))
        self.assertEqual(check.calm(""), ("", False))


def voice(seconds, rate=16_000, seed=1):
    """A made-up voice: a note that wanders, with overtones, louder and quieter three times a second."""
    chance = np.random.default_rng(seed)
    time = np.arange(int(seconds * rate)) / rate
    pitch = 120 + 40 * np.sin(2 * np.pi * 0.7 * time + seed) + np.cumsum(chance.standard_normal(len(time))) * 0.05
    sound = sum(np.sin(2 * np.pi * np.cumsum(pitch * n) / rate) / n for n in range(1, 6))
    sound = sound * (0.3 + 0.2 * np.sin(2 * np.pi * 3 * time))
    return (0.2 * sound + 0.01 * chance.standard_normal(len(time))).astype(np.float32)


@needs_numpy
class Repeats(unittest.TestCase):
    def stuttered(self, seed=3):
        """A voice in which 180 thousandths of a second come twice, 60 apart."""
        sound = voice(4.0, seed=seed)
        again = sound[16_000:18_880]
        return np.concatenate([sound[:18_880], sound[18_880:19_840] * 0.02, again, sound[18_880:]])

    def test_a_sound_that_comes_twice_is_found_where_it_is(self):
        found = listen.repeats(self.stuttered())
        self.assertTrue(found)
        clearest = found[0]
        self.assertAlmostEqual(clearest.first_s, 1.0, delta=0.02)
        self.assertAlmostEqual(clearest.gap_s, 0.24, delta=0.001)
        self.assertAlmostEqual(clearest.second_s - clearest.first_s, clearest.gap_s)
        self.assertGreater(clearest.similarity, 0.99)
        self.assertEqual([r.similarity for r in found], sorted((r.similarity for r in found), reverse=True))
        self.assertEqual(listen.stutter(self.stuttered()), clearest)

    def test_a_voice_that_does_not_repeat_itself_has_none(self):
        for seed in (2, 4):
            with self.subTest(seed=seed):
                self.assertEqual(listen.repeats(voice(4.0, seed=seed)), [])
                self.assertIsNone(listen.stutter(voice(4.0, seed=seed)))

    def test_quiet_is_not_a_repeat_of_quiet(self):
        self.assertEqual(listen.repeats(np.zeros(32_000, dtype=np.float32)), [])
        hiss = (np.random.default_rng(1).standard_normal(32_000) * 0.001).astype(np.float32)
        self.assertEqual(listen.repeats(hiss), [])

    def test_a_repeat_further_apart_than_half_a_second_is_someone_saying_it_again(self):
        sound = voice(4.0, seed=4)
        apart = np.concatenate([sound[:19_200], sound[16_000:19_200] * 0, sound[16_000:19_200] * 0,
                                sound[16_000:19_200] * 0, sound[16_000:19_200], sound[19_200:]])
        self.assertEqual(listen.repeats(apart), [])

    def test_a_sound_too_short_to_hold_a_repeat_has_none(self):
        self.assertEqual(listen.repeats(voice(0.2)), [])

    def test_how_alike_is_alike_enough_can_be_moved(self):
        sound = self.stuttered()
        self.assertGreaterEqual(len(listen.repeats(sound, least_similarity=0.5)), len(listen.repeats(sound)))
        self.assertEqual(listen.repeats(sound, least_gap=0.3), [])


@needs_numpy
class Endings(unittest.TestCase):
    RATE = 24_000

    def test_a_line_that_dies_away_ended(self):
        sound = voice(4.0, self.RATE)
        sound = sound * np.concatenate([np.ones(len(sound) - 12_000), np.linspace(1, 0, 12_000) ** 3]).astype(np.float32)
        stopped, level = listen.cut_off(sound, self.RATE)
        self.assertFalse(stopped)
        self.assertLess(level, -40)

    def test_a_line_stopped_in_the_middle_of_a_sound_was_cut_off(self):
        stopped, level = listen.cut_off(voice(4.0, self.RATE), self.RATE)
        self.assertTrue(stopped)
        self.assertGreater(level, -20)

    def test_quiet_after_the_last_sound_is_not_the_ending(self):
        sound = np.concatenate([voice(4.0, self.RATE), np.zeros(self.RATE, dtype=np.float32)])
        self.assertTrue(listen.cut_off(sound, self.RATE)[0])

    def test_a_short_line_is_not_judged(self):
        self.assertEqual(listen.cut_off(voice(2.0, self.RATE), self.RATE), (False, -120.0))
        self.assertTrue(listen.cut_off(voice(2.0, self.RATE), self.RATE, least_seconds=1.0)[0])

    def test_nothing_was_not_cut_off(self):
        self.assertEqual(listen.cut_off(np.zeros(0, dtype=np.float32), self.RATE), (False, -120.0))
        self.assertEqual(listen.cut_off(np.zeros(5 * self.RATE, dtype=np.float32), self.RATE), (False, -120.0))


@needs_numpy
class Beginnings(unittest.TestCase):
    RATE = 24_000

    def test_the_quiet_before_a_line_is_taken_away(self):
        line = voice(1.0, self.RATE)
        cleaned = listen.clean_start(np.concatenate([np.zeros(2000, dtype=np.float32), line]), self.RATE)
        self.assertAlmostEqual(len(cleaned), len(line), delta=50)

    def test_a_click_before_the_voice_is_cut_away(self):
        click = (np.random.default_rng(2).standard_normal(60) * 0.5).astype(np.float32)
        line = voice(1.0, self.RATE)
        cleaned = listen.clean_start(np.concatenate([click, np.zeros(300, dtype=np.float32), line]), self.RATE)
        self.assertLess(len(cleaned), len(line) + 300)
        self.assertLess(float(np.abs(cleaned[:240]).max()), 0.2, "the click is gone")

    def test_a_line_with_no_click_keeps_its_start(self):
        line = voice(1.0, self.RATE)
        cleaned = listen.clean_start(line, self.RATE)
        self.assertAlmostEqual(len(cleaned), len(line), delta=50)
        self.assertTrue(np.array_equal(cleaned[-12_000:], line[-12_000:]))

    def test_it_fades_in_and_leaves_what_it_was_given_alone(self):
        line = voice(1.0, self.RATE)
        before = line.copy()
        cleaned = listen.clean_start(line, self.RATE)
        self.assertEqual(float(cleaned[0]), 0.0)
        self.assertTrue(np.array_equal(line, before))

    def test_nothing_stays_nothing(self):
        self.assertEqual(len(listen.clean_start(np.zeros(0, dtype=np.float32), self.RATE)), 0)
        self.assertEqual(len(listen.clean_start(np.zeros(100, dtype=np.float32), self.RATE)), 100)

    def test_how_loud_a_line_begins(self):
        line = voice(1.0, self.RATE)
        self.assertLess(listen.start_level(listen.clean_start(line, self.RATE), self.RATE), 0.0)
        loud = np.concatenate([np.ones(10, dtype=np.float32), line])
        self.assertEqual(listen.start_level(loud, self.RATE), 0.0)
        self.assertEqual(listen.start_level(np.zeros(100, dtype=np.float32), self.RATE), -120.0)


@needs_numpy
class Joining(unittest.TestCase):
    RATE = 24_000

    def test_speech_is_faded_across_speech(self):
        first, second = np.ones(self.RATE, dtype=np.float32), np.ones(self.RATE, dtype=np.float32) * 0.5
        whole = listen.join([("text", first), ("text", second)], self.RATE)
        self.assertEqual(len(whole), 2 * self.RATE - 720)
        seam = whole[self.RATE - 720:self.RATE]
        self.assertEqual(float(whole[self.RATE - 721]), 1.0)
        self.assertEqual(float(whole[self.RATE]), 0.5)
        self.assertAlmostEqual(float(seam[0]), 1.0, places=5)
        self.assertAlmostEqual(float(seam[-1]), 0.5, places=5)

    def test_the_fade_keeps_the_loudness_of_two_unlike_sounds(self):
        chance = np.random.default_rng(5)
        first = chance.standard_normal(self.RATE).astype(np.float32)
        second = chance.standard_normal(self.RATE).astype(np.float32)
        whole = listen.join([("text", first), ("text", second)], self.RATE)
        seam = whole[self.RATE - 720:self.RATE]
        self.assertAlmostEqual(float(np.sqrt(np.square(seam).mean())), 1.0, delta=0.15)

    def test_a_pause_keeps_its_whole_length(self):
        first, quiet = np.ones(self.RATE, dtype=np.float32), np.zeros(4800, dtype=np.float32)
        whole = listen.join([("text", first), ("break", quiet), ("text", first)], self.RATE)
        self.assertEqual(len(whole), 2 * self.RATE + 4800)
        self.assertFalse(whole[self.RATE:self.RATE + 4800].any())
        self.assertEqual(float(whole[self.RATE - 1]), 1.0)

    def test_one_piece_is_itself_and_none_is_an_error(self):
        piece = voice(0.5, self.RATE)
        self.assertTrue(np.array_equal(listen.join([("text", piece)], self.RATE), piece))
        with self.assertRaises(ValueError):
            listen.join([], self.RATE)

    def test_a_piece_shorter_than_the_fade_is_faded_over_all_of_it(self):
        whole = listen.join([("text", np.ones(100, dtype=np.float32)), ("text", np.ones(self.RATE, dtype=np.float32))],
                            self.RATE)
        self.assertEqual(len(whole), self.RATE)


if __name__ == "__main__":
    unittest.main()
