"""A model's own voices mixed towards a recording.

Nothing here speaks or listens. For the climb, a stand-in says a line as
two tones, as loud as the two numbers its voice is, and another hears how
loud each tone is: so a voice that is 0.6 of one and 0.8 of the other can
be worked out by hand, and the climb has somewhere real to get to. For the
command around it, a stand-in writes made-up talk and another says what it
heard.
"""

import json
import tempfile
import unittest
from pathlib import Path

from constructive_media.speech import GivenVoices, SpeechError, Voice
from constructive_media.speech import mimic as mimicking

from .support import HAVE_NUMPY, Listener, Speaker, needs_ffmpeg, needs_numpy, syllables
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm, voice

LOW, HIGH = 400.0, 1000.0
OWN = {"af_east": [1.0, 0.0], "af_north": [0.0, 1.0], "bm_easterly": [1.0, 0.1]}


def tones(row, rate=24_000, seconds=1.0):
    at = np.arange(int(rate * seconds)) / rate
    return (0.4 * (row[0] * np.sin(2 * np.pi * LOW * at) + row[1] * np.sin(2 * np.pi * HIGH * at))).astype(np.float32)


class Mixer(Speaker):
    """A stand-in for a model with voices of its own, each of them two numbers."""

    suffix = ".safetensors"

    def __init__(self, own=None, wrong=None, sounds=None):
        super().__init__(wrong)
        self.own, self.sounds, self.written = dict(own or OWN), sounds, []

    def voices(self):
        return sorted(self.own)

    def table(self, voice):
        return np.asarray(self.own[voice] if voice in self.own else json.loads(Path(voice).read_text()))

    def write(self, table, target, *, of=()):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(np.asarray(table).tolist()))
        self.written.append((target.name, list(of)))
        return target

    def say(self, text, target, *, voice, seed=None):
        if self.sounds is None:
            return super().say(text, target, voice=voice, seed=seed)
        self.asked.append((text, voice, seed))
        return pcm.write(target, self.sounds(self.table(voice.profile)), 24_000)


class Ear:
    """A stand-in for a speaker model: a voice is how loud each of two tones is in it."""

    name, model = "stand-in", "made-up-ear"

    def embed(self, sounds):
        rows = []
        for sound in sounds:
            at = np.arange(len(sound)) / 16_000
            row = [abs(np.dot(sound, np.exp(-2j * np.pi * pitch * at))) for pitch in (LOW, HIGH)]
            rows.append(np.asarray(row) / (np.linalg.norm(row) or 1.0))
        return np.asarray(rows, dtype=np.float32)


class Flat:
    """A stand-in for a speaker model that hears every voice as the same one."""

    name, model = "stand-in", "made-up-ear"

    def embed(self, sounds):
        return np.asarray([[0.6, 0.8]] * len(sounds), dtype=np.float32)


@needs_numpy
@needs_ffmpeg
class ClimbTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.recording = pcm.write(self.folder / "ada.wav", tones([0.6, 0.8]), 24_000)
        self.speaker = Mixer(sounds=tones)

    def mimic(self, **how):
        how.setdefault("probes", ("Bring the boats in.",))
        return mimicking.mimic(self.recording, self.speaker, embedder=Ear(), folder=self.folder / "tried", **how)

    def test_every_voice_is_heard_and_the_nearest_comes_first(self):
        found = self.mimic(rounds=0)
        self.assertEqual([name for name, _ in found.heard], ["af_north", "bm_easterly", "af_east"])
        for (_, alike), wanted in zip(found.heard, (0.8, 0.677, 0.6)):
            self.assertAlmostEqual(alike, wanted, places=2)
        self.assertEqual((found.of, found.tried, found.kept), ([("af_north", 1.0)], 0, 0))
        self.assertAlmostEqual(found.alike, 0.8, places=2)
        self.assertEqual(found.table.tolist(), [0.0, 1.0])

    def test_a_mix_is_found_that_is_nearer_than_any_one_voice(self):
        said = []
        found = self.mimic(rounds=60, progress=said.append)
        self.assertGreater(found.alike, 0.98)
        self.assertEqual((found.tried, found.of[0][0]), (60, "af_north"))
        self.assertGreater(found.kept, 0)
        self.assertAlmostEqual(sum(share for _, share in found.of), 1.0)
        # What it is a mix of is what it says it is.
        row = sum(share * np.asarray(OWN[name]) for name, share in found.of)
        self.assertEqual([round(float(value), 4) for value in found.table], [round(float(value), 4) for value in row])
        self.assertAlmostEqual(row[1] / row[0], 0.8 / 0.6, delta=0.35)
        self.assertEqual(said[0], "heard 3 voices; the nearest is af_north, 0.80 alike")
        self.assertRegex(said[-1], r"^mix \d+: \d\.\d\d alike$")

    def test_the_same_seed_finds_the_same_mix_and_another_looks_elsewhere(self):
        one, same, other = self.mimic(rounds=12, seed=5), self.mimic(rounds=12, seed=5), self.mimic(rounds=12, seed=6)
        self.assertEqual(one.of, same.of)
        self.assertNotEqual(one.of, other.of)
        self.assertEqual({seed for _, _, seed in self.speaker.asked}, {5, 6})

    def test_a_voice_is_heard_over_every_sentence_there_is_to_say(self):
        self.mimic(rounds=0, probes=("One.", "Two."))
        self.assertEqual([text for text, _, _ in self.speaker.asked], ["One.", "Two."] * 3)

    def test_the_voices_to_choose_from_and_how_many_are_mixed_can_be_narrowed(self):
        found = self.mimic(rounds=20, among=["af_east", "bm_easterly"])
        self.assertEqual({name for name, _ in found.heard}, {"af_east", "bm_easterly"})
        self.assertLessEqual({name for name, _ in found.of}, {"af_east", "bm_easterly"})
        alone = self.mimic(rounds=20, of=1)
        self.assertEqual((alone.of, alone.tried), ([("af_north", 1.0)], 0))

    def test_nothing_is_left_of_what_was_tried_but_in_the_folder_given(self):
        self.mimic(rounds=3)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["ada.wav", "tried"])
        self.assertEqual({name for name, _ in self.speaker.written}, {"trial.safetensors"})

    def test_what_cannot_be_mixed_is_refused(self):
        for how, why in (({"of": 0}, "one voice or more"), ({"rounds": -1}, "one voice or more"),
                         ({"probes": ()}, "nothing to say"), ({"among": []}, "no voices of its own")):
            with self.assertRaises(SpeechError, msg=how) as raised:
                self.mimic(**how)
            self.assertIn(why, str(raised.exception))

    def test_an_adapter_with_no_voices_of_its_own_is_refused(self):
        self.assertIs(mimicking.able(self.speaker), self.speaker)
        with self.assertRaises(SpeechError) as raised:
            mimicking.able(Speaker())
        self.assertIn("the stand-in speech adapter has no voices of its own to mix", str(raised.exception))
        self.assertIn("kokoro", str(raised.exception))

    def test_a_share_that_is_next_to_nothing_is_not_told(self):
        self.assertEqual(mimicking.told([("a", 0.9), ("b", 0.09), ("c", 0.01)]), [("a", 0.9), ("b", 0.09)])


@needs_numpy
@needs_ffmpeg
class MimicTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        pcm.write(self.folder / "ada.wav", syllables(2.0, 24_000), 24_000, bits=16)
        self.voices = GivenVoices([
            Voice("ada", profile="ada", reference_audio=str(self.folder / "ada.wav"), reference_text="One."),
            Voice("lin", profile="a-voice-the-server-has"),
            Voice("gone", profile="gone", reference_audio=str(self.folder / "gone.wav")),
        ])
        self.kept, self.engines = [], []

    def stand_ins(self, hears=None, speaker=None):
        speaker = speaker or Mixer()
        return speaker, {"voices": self.voices, "synthesizer": lambda engine: self.engines.append(engine) or speaker,
                         "transcriber": Listener(speaker, hears), "embedder": Flat(), "rounds": 2,
                         "store": lambda kept, **how: self.kept.append((kept, how))}

    def test_a_table_is_written_with_words_said_in_it_and_what_it_is_made_of(self):
        speaker, how = self.stand_ins()
        target = self.folder / "out" / "ada.safetensors"
        told = voice.mimic(str(self.folder / "ada.wav"), target, words="The lantern is lit.", **how)
        self.assertEqual(sorted(path.name for path in target.parent.iterdir()),
                         ["ada.mimic.json", "ada.safetensors", "ada.wav"])
        self.assertEqual((told["output"], told["sample"], told["recipe"]),
                         (str(target), str(target.with_suffix(".wav")), str(target.with_suffix(".mimic.json"))))
        self.assertEqual((told["status"], told["attempts"], told["seed"], told["engine"], told["model"]),
                         ("clean", 1, voice.SEED, "stand-in", "made-up-voice"))
        self.assertEqual((told["like"], told["recording"], told["voices"], told["tried"]),
                         ("ada.wav", str(self.folder / "ada.wav"), 3, 2))
        self.assertEqual((told["of"], told["kept"]), ([{"voice": "af_east", "share": 1.0}], None))
        self.assertEqual((told["alike"], told["found_at"], told["nearest"]),
                         (1.0, 1.0, {"voice": "af_east", "alike": 1.0}))
        self.assertEqual(json.loads(target.read_text()), [1.0, 0.0])
        recipe = json.loads(target.with_suffix(".mimic.json").read_text())
        self.assertEqual(recipe, {"like": "ada.wav", "of": told["of"], "alike": 1.0, "nearest": told["nearest"],
                                  "seed": voice.SEED, "model": "made-up-voice"})
        self.assertEqual(self.engines, ["kokoro"])
        # The words are said in the new voice, and then in the nearest there was to begin with.
        self.assertEqual([(text, said_by.profile) for text, said_by, _ in speaker.asked[-2:]],
                         [("The lantern is lit.", str(target.resolve())), ("The lantern is lit.", "af_east")])
        self.assertEqual(self.kept, [])

    def test_a_persona_is_mimicked_from_the_clip_their_voice_was_cloned_from(self):
        speaker, how = self.stand_ins()
        told = voice.mimic("Ada", self.folder / "ada.safetensors", **how)
        self.assertEqual((told["like"], told["recording"], told["words"]),
                         ("ada", str(self.folder / "ada.wav"), voice.PASSAGE))

    def test_what_is_asked_of_the_climb_is_passed_on(self):
        speaker, how = self.stand_ins()
        told = voice.mimic("ada", self.folder / "ada.safetensors", words="Quietly now.", seed=9,
                           **{**how, "rounds": 0, "of": 2, "engine": "another"})
        self.assertEqual((told["tried"], told["seed"], self.engines), (0, 9, ["another"]))
        self.assertEqual({seed for _, _, seed in speaker.asked}, {9})

    def test_a_kept_voice_is_the_table_and_the_adapter_that_speaks_from_it(self):
        speaker, how = self.stand_ins()
        target = self.folder / "voices" / "little-ada.safetensors"
        told = voice.mimic("ada", target, keep="Little Ada", emotion=None, **how)
        self.assertEqual(told["kept"], {"persona": "little-ada", "emotion": "neutral"})
        (kept, how_kept), = self.kept
        self.assertEqual(kept, Voice("little-ada", "neutral", "stand-in", str(target.resolve())))
        self.assertEqual(how_kept, {"new": True})

    def test_nothing_is_kept_of_a_voice_no_take_of_which_passed(self):
        speaker, how = self.stand_ins(hears=lambda text, seed: "ba ko mi ru te")
        told = voice.mimic(str(self.folder / "ada.wav"), self.folder / "kept.safetensors", keep="little ada",
                           attempts=2, more_seeds=0, **how)
        self.assertEqual((told["status"], told["kept"], self.kept), ("needs_review", None, []))

    def test_what_cannot_be_mimicked_is_refused_before_anything_is_said(self):
        speaker, how = self.stand_ins()
        for who, target, why in (
            ("lin", "x.safetensors", "was not made from a clip"),
            ("gone", "x.safetensors", "is not there"),
            ("nobody", "x.safetensors", "nobody"),
            ("ada", "x.wav", "writes a voice as a .safetensors file"),
        ):
            with self.assertRaises(SpeechError, msg=who) as raised:
                voice.mimic(who, self.folder / target, **how)
            self.assertIn(why, str(raised.exception))
        with self.assertRaises(SpeechError) as raised:
            voice.mimic("ada", self.folder / "x.safetensors", words=" ", **how)
        self.assertIn("no words", str(raised.exception))
        plain = Speaker()
        with self.assertRaises(SpeechError) as raised:
            voice.mimic("ada", self.folder / "x.safetensors", **{**how, "synthesizer": lambda engine: plain})
        self.assertIn("has no voices of its own to mix", str(raised.exception))
        self.assertEqual((speaker.asked, plain.asked, speaker.written), ([], [], []))


class CommandTest(unittest.TestCase):
    def test_the_command_is_there_and_says_what_it_takes(self):
        status, out, _ = run("mimic", "--help")
        self.assertEqual(status, 0)
        for said in ("--keep", "--words", "--seed", "--engine", "--of", "--rounds", ".safetensors", "not a clone"):
            self.assertIn(said, out)

    def test_it_takes_somebody_and_somewhere_to_write(self):
        status, _, _ = run("mimic", "ada.flac")
        self.assertEqual(status, 2)

    def test_what_goes_wrong_is_said_in_a_line(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "ada.wav"
            clip.write_bytes(b"RIFF")
            status, _, err = run("mimic", str(clip), str(Path(folder) / "ada.safetensors"), "--engine", "nothing")
        self.assertEqual(status, 1)
        self.assertRegex(err, r"^media: .*nothing")


if __name__ == "__main__":
    unittest.main()
