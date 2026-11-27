"""A voice between other voices.

The arithmetic is tried on rows small enough to work out by hand. Nothing
here speaks or listens: a stand-in hands over made-up numbers for a clip and
writes made-up talk for a row of them, and another says what it heard.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

from constructive_media.speech import GivenVoices, SpeechError, Voice
from constructive_media.speech import blend as blending

from .support import HAVE_NUMPY, Listener, Speaker, needs_ffmpeg, needs_numpy, syllables
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm, voice

EAST, NORTH, UP = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]


def length(row):
    return math.sqrt(sum(value * value for value in row))


class WrittenTest(unittest.TestCase):
    def test_a_voice_is_written_with_its_share_or_without(self):
        self.assertEqual(blending.part("ada:0.35"), ("ada", 0.35))
        self.assertEqual(blending.part("ada"), ("ada", 1.0))
        self.assertEqual(blending.part("clips/ada.flac:2"), ("clips/ada.flac", 2.0))
        self.assertEqual(blending.part("ada:0"), ("ada", 0.0))

    def test_a_colon_that_is_not_before_a_number_is_part_of_the_name(self):
        self.assertEqual(blending.part("take:two.wav"), ("take:two.wav", 1.0))
        self.assertEqual(blending.part("take:two.wav:3"), ("take:two.wav", 3.0))

    def test_a_share_below_nothing_and_a_voice_with_no_name_are_refused(self):
        for written in ("ada:-1", "ada:nan", "ada:inf", " "):
            with self.assertRaises(SpeechError, msg=written):
                blending.part(written)

    def test_shares_are_in_proportion(self):
        self.assertEqual(blending.shares([1, 3]), [0.25, 0.75])
        for weights in ([0, 0], [1, -1], [float("nan"), 1]):
            with self.assertRaises(ValueError, msg=weights):
                blending.shares(weights)


class SlerpTest(unittest.TestCase):
    def near(self, found, wanted, places=9):
        self.assertEqual(len(found), len(wanted))
        for one, other in zip(found, wanted):
            self.assertAlmostEqual(one, other, places=places)

    def test_two_voices_meet_on_the_arc_and_not_on_the_line(self):
        half = blending.slerp([EAST, NORTH], [1, 1])
        self.near(half, [math.sqrt(0.5), math.sqrt(0.5), 0.0])
        self.assertAlmostEqual(length(half), 1.0)   # the middle of the straight line is at 0.707

    def test_a_share_is_how_far_along_the_arc(self):
        found = blending.slerp([EAST, NORTH], [0.35, 0.65])
        self.assertAlmostEqual(math.degrees(math.atan2(found[1], found[0])), 0.65 * 90, places=7)
        one, other = [0.6, 0.8], [0.8, -0.6]
        angle = math.acos(sum(a * b for a, b in zip(one, other)))
        for along in (0.1, 0.5, 0.8):
            wanted = [(math.sin((1 - along) * angle) * a + math.sin(along * angle) * b) / math.sin(angle)
                      for a, b in zip(one, other)]
            self.near(blending.slerp([one, other], [1 - along, along]), wanted)

    def test_all_of_one_voice_is_that_voice(self):
        self.near(blending.slerp([EAST, NORTH], [1, 0]), EAST)
        self.near(blending.slerp([EAST, NORTH], [0, 2]), NORTH)
        self.near(blending.slerp([NORTH, NORTH], [1, 3]), NORTH)

    def test_the_answer_is_as_long_as_the_voices_are_by_their_shares(self):
        found = blending.slerp([[2.0, 0.0], [0.0, 4.0]], [1, 1])
        self.assertAlmostEqual(length(found), 3.0)
        self.assertAlmostEqual(length(blending.slerp([[2.0, 0.0], [0.0, 4.0]], [3, 1])), 2.5)

    def test_three_voices_meet_at_one_place_whatever_order_they_come_in(self):
        self.near(blending.slerp([EAST, NORTH, UP], [1, 1, 1]), [math.sqrt(1 / 3)] * 3)
        one = blending.slerp([EAST, NORTH, UP], [0.2, 0.3, 0.5])
        other = blending.slerp([UP, EAST, NORTH], [0.5, 0.2, 0.3])
        self.near(one, other, places=7)
        self.assertGreater(one[2], one[1])
        self.assertGreater(one[1], one[0])

    def test_what_cannot_be_blended_is_refused(self):
        for rows, weights in (([], []), ([EAST], [1, 1]), ([EAST, [1.0, 0.0]], [1, 1]),
                              ([EAST, [0.0, 0.0, 0.0]], [1, 1]), ([[1.0, 0.0], [-1.0, 0.0]], [1, 1])):
            with self.assertRaises(ValueError, msg=(rows, weights)):
                blending.slerp(rows, weights)
        # A voice with no share is not looked at, whatever it is.
        self.near(blending.slerp([EAST, [0.0, 0.0, 0.0]], [1, 0]), EAST)


class Blender(Speaker):
    """A stand-in for a model that hears a clip as numbers, and says a line from numbers."""

    def __init__(self, heard_as, wrong=None):
        super().__init__(wrong)
        self.heard_as, self.embedded, self.rows = heard_as, [], []

    def embed(self, recording):
        self.embedded.append(Path(recording).name)
        return list(self.heard_as[Path(recording).name])

    def say_as(self, text, target, *, embedding, seed=None):
        self.rows.append(list(embedding))
        return self.say(text, target, voice=None, seed=seed)


class Ear:
    """A stand-in for a speaker model: what it was just made to say is 0.6 like anything else."""

    name, model = "stand-in", "made-up-ear"

    def embed(self, sounds):
        return np.asarray([[1.0, 0.0], [0.6, 0.8]], dtype=np.float32)


@needs_numpy
@needs_ffmpeg
class BlendTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name in ("ada", "grace"):
            pcm.write(self.folder / f"{name}.wav", syllables(2.0, 24_000), 24_000, bits=16)
        self.voices = GivenVoices([
            Voice("ada", profile="ada", reference_audio=str(self.folder / "ada.wav"), reference_text="One."),
            Voice("grace", profile="grace", reference_audio=str(self.folder / "grace.wav"), reference_text="Two."),
            Voice("lin", profile="a-voice-the-server-has"),
            Voice("gone", profile="gone", reference_audio=str(self.folder / "gone.wav")),
        ])
        self.kept = []

    def stand_ins(self, hears=None, speaker=None):
        speaker = speaker or Blender({"ada.wav": EAST, "grace.wav": NORTH})
        return speaker, {"voices": self.voices, "synthesizer": lambda engine: speaker,
                         "transcriber": Listener(speaker, hears), "embedder": Ear(),
                         "store": lambda kept, **how: self.kept.append((kept, how))}

    def test_a_blend_is_said_from_the_row_between_its_voices(self):
        speaker, how = self.stand_ins()
        told = voice.blend([("ada", 0.35), ("grace", 0.65)], self.folder / "out" / "between.wav",
                           words="The lantern is lit.", **how)
        self.assertEqual(speaker.embedded, ["ada.wav", "grace.wav"])
        self.assertEqual(len(speaker.rows), 1)
        self.assertAlmostEqual(math.degrees(math.atan2(speaker.rows[0][1], speaker.rows[0][0])), 0.65 * 90, places=6)
        self.assertEqual((told["status"], told["attempts"], told["seed"], told["model"]),
                         ("clean", 1, voice.SEED, "made-up-voice"))
        self.assertEqual([(one["who"], one["share"], one["alike"]) for one in told["of"]],
                         [("ada", 0.35, 0.6), ("grace", 0.65, 0.6)])
        self.assertEqual(told["output"], str(self.folder / "out" / "between.wav"))
        self.assertEqual(pcm.shape(self.folder / "out" / "between.wav"), (1, 24_000))
        self.assertEqual((told["kept"], self.kept), (None, []))
        self.assertEqual(sorted(path.name for path in (self.folder / "out").iterdir()), ["between.wav"])

    def test_a_recording_can_be_blended_as_well_as_a_persona_and_the_seed_is_as_asked(self):
        speaker, how = self.stand_ins()
        told = voice.blend([(str(self.folder / "grace.wav"), 1.0), ("ada", 3.0)], self.folder / "two.flac",
                           words="Quietly now.", seed=9, **how)
        self.assertEqual([(one["who"], one["share"]) for one in told["of"]],
                         [(str(self.folder / "grace.wav"), 0.25), ("ada", 0.75)])
        self.assertEqual(told["seed"], 9)
        self.assertEqual((self.folder / "two.flac").read_bytes()[:4], b"fLaC")

    def test_with_nothing_to_say_it_says_a_passage_long_enough_to_clone_from(self):
        speaker, how = self.stand_ins()
        told = voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "passage.wav", **how)
        self.assertEqual(told["words"], voice.PASSAGE)
        self.assertEqual(speaker.asked[0][0], voice.PASSAGE)
        self.assertTrue(voice.GOOD_S[0] <= pcm.seconds(self.folder / "passage.wav") <= voice.GOOD_S[1])

    def test_a_take_that_fails_is_said_again_from_the_same_row(self):
        speaker, how = self.stand_ins(hears=lambda text, seed: "ba ko mi ru te" if seed == voice.SEED else text)
        told = voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "again.wav",
                           words="The lantern is lit at dusk.", **how)
        self.assertEqual((told["attempts"], told["status"]), (2, "healed"))
        self.assertEqual(speaker.rows[0], speaker.rows[1])

    def test_a_kept_blend_is_a_clip_its_words_what_it_is_made_of_and_a_voice(self):
        speaker, how = self.stand_ins()
        clip = self.folder / "voices" / "ada-grace" / "neutral.wav"
        told = voice.blend([("ada", 0.35), ("grace", 0.65)], clip, keep="Ada Grace", seed=31, **how)
        self.assertEqual(pcm.shape(clip), (1, voice.RATE))
        self.assertEqual(clip.with_suffix(".txt").read_text(), voice.PASSAGE + "\n")
        recipe = json.loads(clip.with_suffix(".blend.json").read_text())
        self.assertEqual((recipe["seed"], recipe["model"], recipe["words"]), (31, "made-up-voice", voice.PASSAGE))
        self.assertEqual([(one["who"], one["share"]) for one in recipe["of"]], [("ada", 0.35), ("grace", 0.65)])
        self.assertEqual(told["kept"], {"persona": "ada-grace", "emotion": "neutral",
                                        "said_file": str(clip.with_suffix(".txt")),
                                        "recipe": str(clip.with_suffix(".blend.json"))})
        (kept, how_kept), = self.kept
        self.assertEqual(kept, Voice("ada-grace", "neutral", None, "ada-grace", str(clip.resolve()), voice.PASSAGE))
        self.assertEqual(how_kept, {"new": True})

    def test_a_kept_blend_speaks_in_the_emotion_and_the_adapter_asked_for(self):
        speaker, how = self.stand_ins()
        asked = []
        how["synthesizer"] = lambda engine: asked.append(engine) or speaker
        voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "warm.wav", keep="between", emotion="warm",
                    engine="mlx", **how)
        self.assertEqual(asked, ["mlx"])
        self.assertEqual((self.kept[0][0].emotion, self.kept[0][0].engine), ("warm", "mlx"))

    def test_nothing_is_kept_of_a_blend_no_take_of_which_passed(self):
        speaker, how = self.stand_ins(hears=lambda text, seed: "ba ko mi ru te")
        told = voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "kept.wav", keep="between", attempts=2,
                           more_seeds=0, **how)
        self.assertEqual((told["status"], told["output"], told["kept"]), ("needs_review", None, None))
        self.assertEqual(self.kept, [])
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["ada.wav", "grace.wav"])

    def test_how_alike_is_left_out_where_nothing_can_hear_it(self):
        class Deaf:
            def embed(self, sounds):
                raise ModuleNotFoundError("No module named 'onnxruntime'")

        speaker, how = self.stand_ins()
        told = voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "a.wav", words="The lantern is lit.",
                           **{**how, "embedder": Deaf()})
        self.assertEqual([one["alike"] for one in told["of"]], [None, None])
        told = voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "b.wav", words="The lantern is lit.",
                           score=False, **how)
        self.assertEqual([one["alike"] for one in told["of"]], [None, None])

    def test_what_cannot_be_blended_is_refused_and_says_why(self):
        speaker, how = self.stand_ins()
        for sources, said in (
            ([("ada", 1.0)], "two voices or more"),
            ([("ada", 1.0), ("grace", 0.0)], "two voices or more"),
            ([("ada", 1.0), ("lin", 1.0)], "one a server has of its own"),
            ([("ada", 1.0), ("gone", 1.0)], "is not there"),
            ([("ada", 1.0), ("nobody", 1.0)], "nobody"),
        ):
            with self.assertRaises(SpeechError, msg=sources) as raised:
                voice.blend(sources, self.folder / "x.wav", **how)
            self.assertIn(said, str(raised.exception))
        with self.assertRaises(SpeechError) as raised:
            voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "x.wav", words=" ", **how)
        self.assertIn("no words", str(raised.exception))
        self.assertEqual((speaker.embedded, speaker.asked), ([], []))

    def test_an_adapter_that_does_not_hear_a_voice_as_numbers_is_refused(self):
        plain = Speaker()
        _, how = self.stand_ins()
        how["synthesizer"] = lambda engine: plain
        with self.assertRaises(SpeechError) as raised:
            voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "x.wav", **how)
        self.assertIn("the stand-in speech adapter cannot blend voices", str(raised.exception))
        self.assertIn("mlx", str(raised.exception))
        self.assertEqual(plain.asked, [])

    def test_voices_that_two_models_heard_cannot_be_blended(self):
        speaker, how = self.stand_ins(speaker=Blender({"ada.wav": EAST, "grace.wav": [0.0, 1.0]}))
        with self.assertRaises(SpeechError) as raised:
            voice.blend([("ada", 1.0), ("grace", 1.0)], self.folder / "x.wav", **how)
        self.assertIn("not the same size", str(raised.exception))


class CommandTest(unittest.TestCase):
    def test_the_command_is_there_and_says_what_it_takes(self):
        status, out, _ = run("blend", "--help")
        self.assertEqual(status, 0)
        for said in ("VOICE[:SHARE]", "--keep", "--words", "--seed", "--engine"):
            self.assertIn(said, out)

    def test_a_share_that_is_not_one_is_said_before_anything_is_looked_for(self):
        status, _, err = run("blend", "ada:-1", "grace", "out.wav")
        self.assertEqual(status, 1)
        self.assertIn("media: ada:-1: a share is a number from 0 up", err)

    def test_one_voice_is_not_a_blend(self):
        status, _, err = run("blend", "ada", "out.wav")
        self.assertEqual(status, 1)
        self.assertIn("two voices or more", err)
        status, _, err = run("blend", "out.wav")
        self.assertEqual(status, 2)
