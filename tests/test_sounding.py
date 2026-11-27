"""A word read by its spelling.

What is checked is that every kind of word comes out as something a model
can say, and that the commonest patterns of English spelling are read the
common way. It is not checked against a dictionary: a word a dictionary has
never reaches this.
"""

import types
import unittest

from constructive_media.speech import sounding


class SoundingTest(unittest.TestCase):
    def read(self, words, british=False):
        for word, wanted in words.items():
            self.assertEqual(sounding.out(word, british=british), wanted, msg=word)

    def test_a_made_up_name_is_read_the_way_it_looks(self):
        self.read({"Brimblewick": "bɹˈɪmbᵊlwɪk", "Thistlewood": "θˈɪsᵊlwʊd", "Bingham": "bˈɪŋæm",
                   "Starling": "stˈɑɹlɪŋ", "Yarrow": "jˈæɹO", "Marlowe": "mˈɑɹlO", "Schmidt": "ʃmˈɪt"})

    def test_letters_that_go_together_are_one_sound(self):
        self.read({"knight": "nˈIt", "wren": "ɹˈɛn", "phlox": "flˈɑks", "station": "stˈæʃən", "happy": "hˈæpi"})

    def test_a_silent_e_makes_the_vowel_before_it_long(self):
        self.read({"made": "mˈAd", "time": "tˈIm", "bone": "bˈOn", "rose": "ɹˈOz"})

    def test_a_letter_is_read_by_what_stands_beside_it(self):
        self.read({"cell": "sˈɛl", "gem": "ʤˈɛm", "Mongo": "mˈɑŋɡO", "kingdom": "kˈɪŋdɑm", "xylo": "zˈɪlO",
                   "yes": "jˈɛs", "tulip": "tˈulɪp", "carry": "kˈæɹi", "car": "kˈɑɹ"})

    def test_capitals_and_what_has_no_vowel_are_said_letter_by_letter(self):
        self.read({"SQL": "ˈɛskjˈuˈɛl", "x": "ˈɛks", "iOS": "ˈIˈOˈɛs", "PostgreSQL": "pˈɑstɡɹˈɛskjˈuˈɛl"})

    def test_a_digit_in_a_word_is_said_as_its_number(self):
        self.read({"gpt4o": "ʤˈipˈitˈifˈɔɹˈO", "7": "sˈɛvən"})

    def test_what_belongs_to_somebody_ends_as_speech_does(self):
        self.read({"Wren's": "ɹˈɛnz", "Kate's": "kˈAts", "Pip’s": "pˈɪps"})

    def test_a_mark_over_a_letter_is_read_through_and_a_last_e_with_one_is_said(self):
        self.read({"café": "kˈæfA", "Ångström": "ˈæŋstɹɑm"})

    def test_a_british_voice_is_handed_british_sounds(self):
        self.read({"Starling": "stˈɑːlɪŋ", "Postgres": "pˈɒstɡɹɛs", "Yarrow": "jˈaɹQ", "bone": "bˈQn",
                   "car": "kˈɑː", "carry": "kˈaɹi", "Kubernetes": "kˈubɜːnɛtɛs"}, british=True)

    def test_nothing_is_said_of_what_has_no_letter_in_it(self):
        self.read({"": "", "—": "", "...": "", " ": ""})

    def test_every_sound_written_is_one_the_model_knows(self):
        words = ("Brimblewick Thistlewood Kubernetes Postgres Mongo Bingham SQL gpt4o Schmidt Yarrow café Wren's "
                 "Starling nginx queue Zyxwvut Qwerpolk PostgreSQL Ångström xylophone judge measure thing these "
                 "boy now join about chair vision 0123456789 abcdefghijklmnopqrstuvwxyz").split()
        for word in words:
            self.assertLessEqual(set(sounding.out(word)), sounding.AMERICAN, msg=word)
            self.assertLessEqual(set(sounding.out(word, british=True)), sounding.BRITISH, msg=word)
            self.assertTrue(sounding.out(word), msg=word)


class ReaderTest(unittest.TestCase):
    def test_it_answers_as_a_text_front_end_asks(self):
        reader = sounding.Reader()
        self.assertEqual(reader(types.SimpleNamespace(text="Brimblewick")), ("bɹˈɪmbᵊlwɪk", sounding.Reader.RATING))
        self.assertEqual(reader("Postgres"), ("pˈɑstɡɹɛs", 1))
        self.assertEqual(sounding.Reader(british=True)("Postgres"), ("pˈɒstɡɹɛs", 1))

    def test_it_says_so_when_there_is_nothing_to_read(self):
        self.assertEqual(sounding.Reader()(types.SimpleNamespace(text="—")), (None, None))


if __name__ == "__main__":
    unittest.main()
