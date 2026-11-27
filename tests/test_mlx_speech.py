"""Speech said on this machine through MLX, with a stand-in for the model.

No model is loaded and nothing here needs Apple silicon: the adapter is
handed a stand-in that answers as mlx-audio's models do, and a stand-in for
`mlx.core` that keeps arrays as numpy does.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, speech
from constructive_media.speech import mlx as said_here

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy, settings_in, syllables

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm


def core():
    """What the adapter uses of `mlx.core`, kept as numpy keeps it."""
    seeds = []
    made = types.SimpleNamespace(
        float32="float32", seeds=seeds,
        array=lambda values, dtype=None: np.asarray(values, dtype=np.float32),
        zeros=lambda shape, dtype=None: np.zeros(shape, dtype=np.float32),
        random=types.SimpleNamespace(seed=seeds.append),
    )
    package = types.ModuleType("mlx")
    package.core = made
    return {"mlx": package, "mlx.core": made}


class Model:
    """A stand-in for a speech model: it says a second of tone for each part, and keeps what it was asked."""

    sample_rate = 24_000
    speaker_encoder = object()

    def __init__(self, parts=1):
        self.parts, self.asked = parts, []

    def generate(self, **asked):
        heard = asked.get("ref_audio")
        self.asked.append({**asked, "heard_as": None if heard is None else self.extract_speaker_embedding(heard, sr=24_000)})
        for _ in range(self.parts):
            yield types.SimpleNamespace(audio=0.2 * np.sin(np.arange(24_000) * 0.05, dtype=np.float32))

    def extract_speaker_embedding(self, heard, sr):
        return np.full((1, 4), float(len(heard)) / sr, dtype=np.float32)


@needs_numpy
@needs_ffmpeg
class MlxSpeechTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        modules = mock.patch.dict(sys.modules, core())
        modules.start()
        self.addCleanup(modules.stop)
        self.seeds = sys.modules["mlx.core"].seeds
        self.model = Model()
        self.speaker = said_here.MlxSpeech(settings=settings_in(self.folder / "home"))
        self.speaker._loaded = self.model
        self.clip = pcm.write(self.folder / "ada.wav", syllables(3.0, 48_000), 48_000)

    def test_a_cloned_voice_is_said_from_its_recording_and_the_words_in_it(self):
        voice = speech.Voice("ada", reference_audio=str(self.clip), reference_text="The lantern is lit at dusk.")
        written = self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=voice, seed=507)
        self.assertEqual((pcm.shape(written), round(pcm.seconds(written), 2)), ((1, 24_000), 1.0))
        (asked,) = self.model.asked
        self.assertEqual((asked["text"], asked["ref_text"]), ("Bring the boats in.", "The lantern is lit at dusk."))
        self.assertEqual(len(asked["ref_audio"]), 3 * 24_000)        # heard at the rate the model listens at
        self.assertEqual((asked["temperature"], asked["top_p"], asked["lang_code"]), (0.3, 0.8, "english"))
        self.assertEqual(asked["max_tokens"], said_here.LEAST_TOKENS)
        self.assertNotIn("voice", asked)
        self.assertEqual(self.seeds, [507])

    def test_a_voice_the_model_has_of_its_own_is_asked_for_by_name(self):
        self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=speech.Voice("ada", profile="keeper"))
        (asked,) = self.model.asked
        self.assertEqual(asked["voice"], "keeper")
        self.assertNotIn("ref_audio", asked)
        self.assertEqual(self.seeds, [])

    def test_a_voice_with_nothing_to_speak_from_is_refused(self):
        with self.assertRaisesRegex(speech.SpeechError, "neither a recording to clone nor a profile"):
            self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=speech.Voice("ada"))
        gone = speech.Voice("ada", reference_audio=str(self.folder / "gone.wav"))
        with self.assertRaisesRegex(speech.SpeechError, "is not there"):
            self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=gone)

    def test_what_a_model_says_in_parts_is_one_recording(self):
        self.speaker._loaded = Model(parts=3)
        written = self.speaker.say("Bring the boats in.", self.folder / "line.wav",
                                   voice=speech.Voice("ada", profile="keeper"))
        self.assertAlmostEqual(pcm.seconds(written), 3.0, delta=0.01)

    def test_a_model_that_fails_or_says_nothing_is_an_error_of_speech(self):
        self.speaker._loaded = Model(parts=0)
        with self.assertRaisesRegex(speech.SpeechError, "nothing to hear"):
            self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=speech.Voice("ada", profile="keeper"))

        class Broken(Model):
            def generate(self, **asked):
                raise ValueError("out of memory")

        self.speaker._loaded = Broken()
        with self.assertRaisesRegex(speech.SpeechError, "could not say the line: out of memory"):
            self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=speech.Voice("ada", profile="keeper"))

    def test_a_long_line_may_run_longer_and_no_line_runs_for_ever(self):
        self.assertEqual(said_here.most_tokens("Yes."), said_here.LEAST_TOKENS)
        self.assertEqual(said_here.most_tokens(" ".join(["word"] * 40)), 500)

    def test_what_a_line_is_said_with_can_be_changed(self):
        speaker = said_here.MlxSpeech(settings=settings_in(
            self.folder / "home", extras={"speech": '{"temperature": 0.7, "language": "french"}'},
            models={"speech": "a-folder-of-weights"}))
        speaker._loaded = self.model
        self.assertEqual(speaker.model, "a-folder-of-weights")
        speaker.say("Bring the boats in.", self.folder / "line.wav", voice=speech.Voice("ada", profile="keeper"))
        (asked,) = self.model.asked
        self.assertEqual((asked["temperature"], asked["top_p"], asked["lang_code"]), (0.7, 0.8, "french"))
        for wrong, why in (("{", "is not JSON"), ("[1]", "should be a JSON object")):
            with self.assertRaisesRegex(speech.SpeechError, why):
                said_here.MlxSpeech(settings=settings_in(self.folder / "home", extras={"speech": wrong}))

    def test_a_voice_is_heard_as_numbers_and_said_from_them(self):
        heard = self.speaker.embed(self.clip)
        self.assertEqual(heard, [3.0] * 4)
        blend = [0.5, 0.25, 0.0, 1.0]
        self.speaker.say_as("Bring the boats in.", self.folder / "line.wav", embedding=blend, seed=3)
        (asked,) = self.model.asked
        self.assertEqual(asked["heard_as"].tolist(), [blend])        # the voice it was told, not what it heard
        self.assertEqual(len(asked["ref_audio"]), 24_000)
        self.assertFalse(asked["ref_audio"].any())
        # And the model hears for itself again afterwards.
        self.assertEqual(self.speaker.embed(self.clip), [3.0] * 4)

    def test_a_model_that_does_not_hear_voices_as_numbers_cannot_blend(self):
        self.model.speaker_encoder = None
        with self.assertRaisesRegex(speech.SpeechError, "it cannot blend"):
            self.speaker.embed(self.clip)

    def test_one_line_is_said_at_a_time_on_a_machine(self):
        with self.speaker.alone():
            self.assertTrue((self.folder / "home" / "locks" / "mlx.lock").is_file())


class SlotTest(unittest.TestCase):
    def test_it_is_built_in_and_asks_for_what_it_needs_only_when_used(self):
        self.assertIn("mlx", {adapter.name for adapter in adapters.builtins() if adapter.slot == "speech"})
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(sys.modules, {"mlx_audio": None}):
            speaker = said_here.MlxSpeech(settings=settings_in(Path(home)))
            self.assertEqual(speaker.model, said_here.DEFAULT_MODEL)
            with self.assertRaisesRegex(speech.SpeechError, "pip install mlx-audio"):
                speaker.loaded()


if __name__ == "__main__":
    unittest.main()
