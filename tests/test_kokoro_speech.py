"""Speech from a small model with voices of its own, with a stand-in for the model.

No model is loaded, nothing is installed and nothing here needs Apple
silicon: the adapter is handed a stand-in that answers as mlx-audio's
Kokoro does, a stand-in for `mlx.core` that keeps a table as a small file,
and empty modules where the text front end would be.
"""

import contextlib
import io
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, speech
from constructive_media.speech import kokoro, sounding

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy, settings_in

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm

OWN = ("af_heart", "am_adam", "bf_emma", "bm_george", "ef_dora")


def core():
    """What the adapter uses of `mlx.core`, with a table kept as a few lines of JSON."""
    seeds = []

    def save_safetensors(path, arrays, metadata=None):
        held = {name: np.asarray(array).tolist() for name, array in arrays.items()}
        Path(path).write_text(json.dumps({"arrays": held, "about": metadata or {}}))

    def load(path, return_metadata=False):
        held = json.loads(Path(path).read_text())
        arrays = {name: np.asarray(array, dtype=np.float32) for name, array in held["arrays"].items()}
        return (arrays, held["about"]) if return_metadata else arrays

    made = types.SimpleNamespace(
        float32="float32", seeds=seeds, save_safetensors=save_safetensors, load=load,
        array=lambda values, dtype=None: np.asarray(values, dtype=np.float32),
        random=types.SimpleNamespace(seed=seeds.append),
    )
    package = types.ModuleType("mlx")
    package.core = made
    return {"mlx": package, "mlx.core": made}


def front_end(reads=True, espeak=None):
    """Empty modules where misaki and spaCy would be. `espeak` is misaki's espeak module, or None for not there."""
    spacy, util = types.ModuleType("spacy"), types.ModuleType("spacy.util")
    util.is_package = lambda name: reads
    spacy.util = util
    return {"mlx_audio": types.ModuleType("mlx_audio"), "misaki": types.ModuleType("misaki"),
            "misaki.en": types.ModuleType("misaki.en"), "misaki.espeak": espeak, "spacy": spacy, "spacy.util": util}


class Model:
    """A stand-in for Kokoro: a folder of voices, a reader for each language, and a second of tone for a line."""

    sample_rate = 24_000

    def __init__(self, folder, fallback=None):
        self.config = types.SimpleNamespace(model_path=str(folder))
        self.fallback, self.asked, self.readers = fallback, [], {}
        (folder / "voices").mkdir(parents=True)
        for at, name in enumerate(OWN):
            sys.modules["mlx.core"].save_safetensors(folder / "voices" / f"{name}.safetensors",
                                                     {"voice": np.full((3, 1, 4), float(at))})
        (folder / "voices" / "notes.txt").write_text("not a voice")

    def _get_pipeline(self, language):
        if language not in self.readers:
            print(f"Creating new KokoroPipeline for language: {language}")
            logging.getLogger("kokoro").warning("EspeakFallback not Enabled: OOD words will be skipped")
            self.readers[language] = types.SimpleNamespace(g2p=types.SimpleNamespace(fallback=self.fallback))
        return self.readers[language]

    def generate(self, **asked):
        self.asked.append(asked)
        logging.getLogger("kokoro").warning("Truncating to 510 tokens")
        yield types.SimpleNamespace(audio=0.2 * np.sin(np.arange(24_000) * 0.05, dtype=np.float32))


@needs_numpy
@needs_ffmpeg
class KokoroSpeechTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        modules = mock.patch.dict(sys.modules, {**core(), **front_end()})
        modules.start()
        self.addCleanup(modules.stop)
        self.seeds = sys.modules["mlx.core"].seeds
        kokoro.front_end()          # espeak is not installed: something stands where it would be
        self.model = Model(self.folder / "weights")
        self.speaker = self.made()

    def made(self, model=None, **settings):
        speaker = kokoro.KokoroSpeech(settings=settings_in(self.folder / "home", **settings))
        speaker._loaded = model or self.model
        return speaker

    def say(self, profile, text="Bring the boats in.", speaker=None, **how):
        (speaker or self.speaker).say(text, self.folder / "line.wav", voice=speech.Voice("ada", profile=profile), **how)
        return self.model.asked[-1]

    def test_a_voice_of_the_models_own_is_asked_for_by_name_in_its_language(self):
        asked = self.say("af_heart", seed=507)
        self.assertEqual(asked, {"text": "Bring the boats in.", "speed": 1.0, "voice": "af_heart", "lang_code": "a"})
        self.assertEqual((pcm.shape(self.folder / "line.wav"), self.seeds), ((1, 24_000), [507]))
        self.assertEqual(self.say("bm_george")["lang_code"], "b")
        self.assertEqual(self.say("af_heart,am_adam")["voice"], "af_heart,am_adam")

    def test_a_word_no_dictionary_has_is_read_by_its_spelling(self):
        self.say("af_heart")
        self.say("bm_george")
        american, british = (self.model.readers[language].g2p.fallback for language in "ab")
        self.assertIsInstance(american, sounding.Reader)
        self.assertEqual((american.british, british.british), (False, True))
        self.assertEqual(american(types.SimpleNamespace(text="Brimblewick"))[0], "bɹˈɪmbᵊlwɪk")

    def test_a_reader_somebody_installed_is_left_where_it_is(self):
        theirs = object()
        self.model = Model(self.folder / "other", fallback=theirs)
        self.say("af_heart", speaker=self.made())
        self.assertIs(self.model.readers["a"].g2p.fallback, theirs)

    def test_what_the_model_prints_and_warns_of_stays_off_the_screen(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logging.getLogger("kokoro").addHandler(handler)
        self.addCleanup(logging.getLogger("kokoro").removeHandler, handler)
        was = logging.root.manager.disable
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.say("af_heart")
        self.assertEqual((printed.getvalue(), records), ("", []))
        self.assertEqual(logging.root.manager.disable, was)

    def test_a_table_is_written_and_spoken_from_in_the_language_of_most_of_it(self):
        table = np.arange(12, dtype=np.float32).reshape(3, 1, 4)
        mostly_british = self.speaker.write(table, self.folder / "kept" / "ada.safetensors",
                                            of=[("bm_george", 0.6), ("af_heart", 0.3), ("bf_emma", 0.1)])
        self.assertEqual(self.speaker.table(str(mostly_british)).tolist(), table.tolist())
        asked = self.say(str(mostly_british))
        self.assertEqual((asked["voice"], asked["lang_code"]), (str(mostly_british), "b"))
        mostly_american = self.speaker.write(table, self.folder / "b.safetensors",
                                             of=[("af_heart", 0.5), ("bm_george", 0.5)])
        self.assertEqual(self.say(str(mostly_american))["lang_code"], "a")

    def test_a_table_that_does_not_say_what_it_speaks_is_read_in_the_language_set(self):
        british = self.made(adapters={"speech": "kokoro"}, extras={"speech": '{"language": "british", "speed": 1.2}'})
        plain = british.write(np.zeros((3, 1, 4)), self.folder / "plain.safetensors")
        asked = self.say(str(plain), speaker=british)
        self.assertEqual((asked["lang_code"], asked["speed"]), ("b", 1.2))
        sys.modules["mlx.core"].save_safetensors(self.folder / "bare.safetensors", {"voice": np.zeros((3, 1, 4))})
        self.assertEqual(self.say(str(self.folder / "bare.safetensors"))["lang_code"], "a")

    def test_its_own_voices_that_speak_english_can_be_listed_and_handed_over(self):
        self.assertEqual(self.speaker.voices(), ["af_heart", "am_adam", "bf_emma", "bm_george"])
        self.assertEqual(self.speaker.table("bf_emma").tolist(), np.full((3, 1, 4), 2.0).tolist())
        self.assertEqual(self.speaker.suffix, ".safetensors")
        with self.assertRaisesRegex(speech.SpeechError, "has no voice called zz_nobody"):
            self.speaker.table("zz_nobody")

    def test_a_voice_it_cannot_speak_in_is_refused_and_says_what_it_takes(self):
        clip = pcm.write(self.folder / "ada.wav", np.zeros(24_000, dtype=np.float32), 24_000)
        (self.folder / "torn.safetensors").write_text("{")
        for voice, why in (
            (speech.Voice("ada", reference_audio=str(clip)), "does not clone"),
            (speech.Voice("ada", profile="keeper"), "is not one the kokoro speech adapter has: keeper"),
            (speech.Voice("ada"), "it has no profile"),
            (speech.Voice("ada", profile=str(self.folder / "gone.safetensors")), "is not there"),
            (speech.Voice("ada", profile=str(self.folder / "torn.safetensors")), "is not a voice table"),
            (speech.Voice("ada", profile="ef_dora"), "reads English by itself"),
        ):
            with self.assertRaises(speech.SpeechError, msg=why) as raised:
                self.speaker.say("Bring the boats in.", self.folder / "line.wav", voice=voice)
            self.assertIn(why, str(raised.exception))
        self.assertEqual(self.model.asked, [])
        with self.assertRaisesRegex(speech.SpeechError, "is written as a .safetensors file"):
            self.speaker.write(np.zeros((3, 1, 4)), self.folder / "ada.wav")

    def test_another_language_is_read_when_somebody_installed_what_reads_it(self):
        with mock.patch.dict(sys.modules, {"misaki.espeak": types.ModuleType("misaki.espeak")}):
            self.assertEqual(self.say("ef_dora")["lang_code"], "e")

    def test_what_is_set_for_speech_is_taken_only_when_this_is_what_speaks(self):
        said = {"models": {"speech": "a-cloning-model"}, "extras": {"speech": '{"temperature": 0.7}'}}
        other = self.made(adapters={"speech": "mlx"}, **said)
        self.assertEqual((other.model, other.options), (kokoro.DEFAULT_MODEL, kokoro.DEFAULTS))
        chosen = self.made(adapters={"speech": "kokoro"}, models={"speech": "a-folder-of-weights"},
                           extras={"speech": '{"speed": "quick"}'})
        self.assertEqual(chosen.model, "a-folder-of-weights")
        with self.assertRaisesRegex(speech.SpeechError, "speed .* is not a number"):
            self.say("af_heart", speaker=chosen)
        odd = self.made(adapters={"speech": "kokoro"}, extras={"speech": '{"language": "elvish"}'})
        with self.assertRaisesRegex(speech.SpeechError, "does not know the language 'elvish'"):
            odd.write(np.zeros((3, 1, 4)), self.folder / "odd.safetensors")


class SlotTest(unittest.TestCase):
    def test_it_is_built_in_and_permissive(self):
        (found,) = [adapter for adapter in adapters.builtins() if (adapter.slot, adapter.name) == ("speech", "kokoro")]
        self.assertEqual(found.license, "MIT")

    def test_it_asks_for_what_it_needs_only_when_used(self):
        with tempfile.TemporaryDirectory() as home:
            speaker = kokoro.KokoroSpeech(settings=settings_in(Path(home)))
            self.assertEqual(speaker.model, kokoro.DEFAULT_MODEL)
            with mock.patch.dict(sys.modules, {"mlx_audio": None}):
                with self.assertRaisesRegex(speech.SpeechError, "pip install mlx-audio"):
                    speaker.loaded()
            with mock.patch.dict(sys.modules, {**front_end(), "misaki.en": None}):
                with self.assertRaisesRegex(speech.SpeechError, "pip install misaki num2words spacy"):
                    speaker.loaded()
            with mock.patch.dict(sys.modules, front_end(reads=False)):
                with self.assertRaisesRegex(speech.SpeechError, "python -m spacy download en_core_web_sm"):
                    speaker.loaded()

    def test_something_stands_where_the_reader_it_does_without_would_be(self):
        with mock.patch.dict(sys.modules, front_end()):
            kokoro.front_end()
            stand_in = sys.modules["misaki.espeak"]
            self.assertTrue(stand_in.absent)
            with self.assertRaises(RuntimeError):
                stand_in.EspeakFallback(british=False)
        theirs = types.ModuleType("misaki.espeak")
        with mock.patch.dict(sys.modules, front_end(espeak=theirs)):
            kokoro.front_end()
            self.assertIs(sys.modules["misaki.espeak"], theirs)


if __name__ == "__main__":
    unittest.main()
