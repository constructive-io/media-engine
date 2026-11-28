"""A number written out in words.

What is checked is that the words are the ones an English speaker says, and
that the module put where `num2words` would be answers the way the text
front end asks.
"""

import unittest

from constructive_media.speech import numerals


class NumeralsTest(unittest.TestCase):
    def said(self, numbers, **how):
        for number, wanted in numbers.items():
            self.assertEqual(numerals.words(number, **how), wanted, msg=repr(number))

    def test_a_number_is_counted(self):
        self.said({0: "zero", 7: "seven", 13: "thirteen", 20: "twenty", 21: "twenty-one", 99: "ninety-nine",
                   100: "one hundred", 101: "one hundred and one", 342: "three hundred and forty-two",
                   1000: "one thousand", 1001: "one thousand and one", 1100: "one thousand, one hundred",
                   1234: "one thousand, two hundred and thirty-four", 20_000: "twenty thousand",
                   1_000_000: "one million", 1_000_001: "one million and one",
                   2_500_000: "two million, five hundred thousand",
                   7_000_000_042: "seven billion and forty-two", -15: "minus fifteen"})

    def test_a_number_too_long_to_name_is_read_a_figure_at_a_time(self):
        self.assertEqual(numerals.words(10 ** 63), "one vigintillion")
        self.assertEqual(numerals.words(12 * 10 ** 66), "one two" + " zero" * 66)

    def test_a_place_in_a_row(self):
        self.said({1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 8: "eighth", 9: "ninth",
                   12: "twelfth", 20: "twentieth", 21: "twenty-first", 43: "forty-third", 100: "one hundredth",
                   101: "one hundred and first", 1000: "one thousandth", 1_000_000: "one millionth"}, to="ordinal")
        self.said({1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th", 13: "13th", 21: "21st",
                   102: "102nd", 113: "113th"}, to="ordinal_num")

    def test_a_year_is_said_in_two_halves(self):
        self.said({1984: "nineteen eighty-four", 1066: "ten sixty-six", 1900: "nineteen hundred",
                   1905: "nineteen oh-five", 2000: "two thousand", 2005: "two thousand and five",
                   2010: "twenty ten", 2026: "twenty twenty-six", 906: "nine oh-six", 1005: "one thousand and five",
                   42: "forty-two",
                   12_000: "twelve thousand", -44: "forty-four BC"}, to="year")

    def test_what_comes_after_the_point_is_read_a_figure_at_a_time(self):
        self.said({3.14: "three point one four", 0.5: "zero point five", 10.01: "ten point zero one",
                   -2.5: "minus two point five", 0.0000001: "zero point zero zero zero zero zero zero one",
                   2.0: "two", 1500.25: "one thousand, five hundred point two five"})

    def test_a_number_may_be_handed_over_as_it_was_written(self):
        self.said({"1,234": "one thousand, two hundred and thirty-four", " 7 ": "seven", "3.50": "three point five",
                   "12.0": "twelve"})

    def test_what_cannot_be_said_is_refused(self):
        with self.assertRaisesRegex(NotImplementedError, "not as 'currency'"):
            numerals.words(5, to="currency")
        with self.assertRaisesRegex(TypeError, "only a whole number"):
            numerals.words(2.5, to="ordinal")
        with self.assertRaisesRegex(TypeError, "only a whole number"):
            numerals.words(2.5, to="year")
        for nothing in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaisesRegex(ValueError, "not a number that can be said"):
                numerals.words(nothing)


class StandInTest(unittest.TestCase):
    def test_it_answers_the_way_the_package_is_asked(self):
        module = numerals.stand_in()
        self.assertEqual(module.__name__, "num2words")
        self.assertTrue(module.stand_in)
        ask = module.num2words
        self.assertEqual(ask(42), "forty-two")
        self.assertEqual(ask(42, to="cardinal"), "forty-two")
        self.assertEqual(ask(3, to="ordinal"), "third")
        self.assertEqual(ask(3, ordinal=True), "third")
        self.assertEqual(ask(1999, to="year"), "nineteen ninety-nine")
        self.assertEqual(ask(2.75), "two point seven five")
        self.assertEqual(ask(8, lang="en_GB"), "eight")

    def test_it_answers_for_english_only_and_says_where_the_rest_is(self):
        with self.assertRaisesRegex(NotImplementedError, "for 'fr', pip install num2words"):
            numerals.num2words(8, lang="fr")


if __name__ == "__main__":
    unittest.main()
