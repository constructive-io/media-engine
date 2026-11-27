"""Voices, and the client for servers that speak.

No model speaks here. The client is pointed at a small server on this machine
that writes down what it was sent and answers with what the test told it to.
Every voice is made up.
"""

import os
import tempfile
import unittest
from pathlib import Path

from constructive_media import adapters, ffmpeg, speech
from constructive_media.settings import Settings
from constructive_media.speech import GivenVoices, SpeechError, StoredVoices, Synthesizer, Voice, Voices
from constructive_media.speech.http import SpeechServer

from .support import DatabaseTest, make_tone, needs_ffmpeg
from .test_cli import run
from .test_text import Served

ADA = Voice("ada", profile="ada-plain")
HUSHED = Voice("ada", "whisper", profile="ada-hushed")


class Choosing(unittest.TestCase):
    def test_the_voice_for_the_emotion_then_the_neutral_one_then_the_first(self):
        warm, tender = Voice("ada", "warm"), Voice("ada", "tender")
        self.assertIs(speech.pick([warm, ADA, HUSHED], "whisper"), HUSHED)
        self.assertIs(speech.pick([warm, ADA, HUSHED], "playful"), ADA)
        self.assertIs(speech.pick([warm, ADA, HUSHED]), ADA)
        self.assertIs(speech.pick([warm, tender], "playful"), tender)
        self.assertIsNone(speech.pick([], "warm"))

    def test_voices_handed_over_are_found_by_persona(self):
        voices = GivenVoices([ADA, HUSHED, Voice("grace", "warm", profile="grace-warm")])
        self.assertIsInstance(voices, Voices)
        self.assertIs(voices.find("ada", "whisper"), HUSHED)
        self.assertIs(voices.find("ada"), ADA)
        self.assertEqual(voices.find("grace", "whisper").profile, "grace-warm")
        self.assertEqual(voices.emotions(), {"ada": {"neutral", "whisper"}, "grace": {"warm"}})

    def test_a_persona_with_no_voice_cannot_speak(self):
        with self.assertRaises(SpeechError) as raised:
            GivenVoices([ADA]).find("grace")
        self.assertEqual(str(raised.exception), "'grace' has no voice to speak in")

    def test_the_server_client_is_the_one_built_in(self):
        adapter = adapters.registry().describe("speech")
        self.assertEqual((adapter.name, adapter.license), ("http", "MIT"))
        self.assertIn(adapter.license, adapters.PERMISSIVE)


class Keeping(DatabaseTest):
    def test_a_voice_is_written_down_and_found_again(self):
        with self.assertRaises(Exception) as raised:
            speech.keep(ADA)
        self.assertIn("add --new", str(raised.exception))
        self.assertFalse(speech.keep(ADA, new=True))
        self.assertFalse(speech.keep(Voice("ada", "whisper", "made-up-engine", "ada-hushed", "takes/ada.wav",
                                           "The tide comes in at seven.", 31)))
        voices = StoredVoices()
        self.assertIsInstance(voices, Voices)
        # Nothing said which adapter speaks it: the configured one does.
        self.assertEqual(voices.find("ada"), Voice("ada", "neutral", "http", "ada-plain"))
        self.assertEqual(voices.find("ada", "whisper"),
                         Voice("ada", "whisper", "made-up-engine", "ada-hushed", "takes/ada.wav",
                               "The tide comes in at seven.", 31))
        self.assertEqual(voices.find("ada", "playful").profile, "ada-plain")
        self.assertEqual(voices.emotions(), {"ada": {"neutral", "whisper"}})
        self.assertEqual([(kept.voice.emotion, kept.status) for kept in speech.kept()],
                         [("neutral", "ready"), ("whisper", "ready")])

    def test_a_second_voice_for_an_emotion_takes_the_place_of_the_first(self):
        speech.keep(ADA, new=True)
        self.assertTrue(speech.keep(Voice("Ada", profile="ada-again")))
        self.assertEqual(StoredVoices().find("ada").profile, "ada-again")
        self.assertEqual(len(speech.kept()), 1)

    def test_a_voice_that_is_broken_or_in_training_does_not_speak(self):
        speech.keep(ADA, new=True, status="broken")
        speech.keep(HUSHED, status="provisional")
        voices = StoredVoices()
        self.assertEqual(voices.find("ada").profile, "ada-hushed")
        self.assertEqual(voices.emotions(), {"ada": {"whisper"}})
        speech.keep(HUSHED, status="training")
        with self.assertRaises(SpeechError):
            voices.find("ada")
        self.assertEqual(voices.emotions(), {})

    def test_a_voice_needs_what_its_adapter_knows_it_by(self):
        with self.assertRaises(SpeechError) as raised:
            speech.keep(Voice("ada"), new=True)
        self.assertIn("needs a profile", str(raised.exception))

    def test_voices_are_taken_away_one_or_all(self):
        speech.keep(ADA, new=True)
        speech.keep(HUSHED)
        speech.keep(Voice("grace", profile="grace-plain"), new=True)
        self.assertEqual(speech.drop("ada", "whisper"), 1)
        self.assertEqual(speech.drop("ada", "whisper"), 0)
        self.assertEqual(speech.drop("ada"), 1)
        self.assertEqual([kept.voice.persona for kept in speech.kept()], ["grace"])

    def test_the_commands_give_list_and_take_away(self):
        status, out, err = run("voice", "Ada Field", "ada-plain", "--new")
        self.assertEqual((status, out, err), (0, "Ada Field now has a neutral voice: ada-plain\n", ""))
        status, out, _ = run("voice", "ada-field", "ada-hushed", "--emotion", "whisper", "--seed", "31",
                             "--reference", "takes/ada.wav", "--said", "The tide comes in at seven.", "--provisional")
        self.assertEqual((status, out), (0, "ada-field now has a whisper voice: ada-hushed\n"))
        status, out, _ = run("voice", "ada-field", "ada-again")
        self.assertEqual((status, out), (0, "ada-field has a new neutral voice: ada-again\n"))

        status, out, _ = run("voices")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["ada-field", "neutral", "http", "ada-again"])
        self.assertEqual(lines[1].split(maxsplit=4),
                         ["ada-field", "whisper", "http", "ada-hushed", "(seed 31, from a recording, provisional)"])
        self.assertEqual(lines[2], "2 voices for 1 personas")

        # The words of a clip are read from beside it, where `me reference` writes them.
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "ada.flac"
            clip.write_bytes(b"")
            clip.with_suffix(".txt").write_text("The lamp was lit at four.\n", encoding="utf-8")
            self.assertEqual(run("voice", "ada-field", "ada", "--emotion", "warm", "--reference", str(clip))[0], 0)
            warm = StoredVoices().find("ada-field", "warm")
            self.assertEqual((warm.reference_audio, warm.reference_text),
                             (os.path.abspath(clip), "The lamp was lit at four."))
            speech.drop("ada-field", "warm")

        status, out, _ = run("voice", "Ada Field", "--drop", "--emotion", "whisper")
        self.assertEqual((status, out), (0, "1 voices of Ada Field taken away\n"))
        self.assertEqual(run("voice", "Ada Field", "--drop", "--emotion", "whisper")[0], 1)

    def test_the_command_says_what_it_was_not_given(self):
        status, _, err = run("voice", "ada")
        self.assertEqual(status, 2)
        self.assertIn("give the profile", err)
        status, _, err = run("voice", "ada", "ada-plain", "--drop")
        self.assertEqual(status, 2)
        status, _, err = run("voice", "ada", "ada-plain")
        self.assertEqual((status, err), (1, "me: nobody is called 'ada' yet; add --new to add them\n"))
        status, _, err = run("voice", "ada", "ada-plain", "--engine", "nothing-by-this-name", "--new")
        self.assertEqual(status, 1)
        self.assertIn("no 'speech' adapter named 'nothing-by-this-name'", err)


class SpeechServerTest(Served):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def settings(self, **more):
        return Settings.from_env({"CONSTRUCTIVE_MEDIA_URL_SPEECH": self.url + "/",
                                  "CONSTRUCTIVE_MEDIA_MODEL_SPEECH": "made-up-speaker", **more})

    def refused(self, call, *said):
        with self.assertRaises(SpeechError) as raised:
            call()
        for part in said:
            self.assertIn(part, str(raised.exception))
        return str(raised.exception)

    def sound(self, kind="wav"):
        return make_tone(self.folder / f"tone.{kind}", 0.3).read_bytes()

    @needs_ffmpeg
    def test_the_words_and_the_voice_are_sent_and_the_sound_is_written_as_it_came(self):
        sound = self.sound()
        self.server.answer = (200, sound)
        speaker = SpeechServer(settings=self.settings())
        self.assertIsInstance(speaker, Synthesizer)
        self.assertEqual((speaker.name, speaker.model, speaker.url), ("http", "made-up-speaker", self.url))
        target = self.folder / "lines" / "line_001.wav"
        self.assertEqual(speaker.say("The lantern is lit.", target, voice=ADA, seed=507), target)
        self.assertEqual(target.read_bytes(), sound)
        ((path, headers, sent),) = self.server.asked
        self.assertEqual(path, "/v1/audio/speech")
        self.assertEqual(sent, {"model": "made-up-speaker", "input": "The lantern is lit.", "voice": "ada-plain",
                                "response_format": "wav", "seed": 507})
        self.assertNotIn("Authorization", headers)

    @needs_ffmpeg
    def test_no_seed_is_sent_when_there_is_none_or_the_server_is_spared_it(self):
        self.server.answer = (200, self.sound())
        SpeechServer(settings=self.settings()).say("Lit.", self.folder / "a.wav", voice=ADA)
        spared = self.settings(CONSTRUCTIVE_MEDIA_EXTRA_SPEECH='{"seed": null, "speed": 0.95, "model": "another"}')
        SpeechServer(settings=spared).say("Lit.", self.folder / "b.wav", voice=ADA, seed=507)
        self.assertNotIn("seed", self.server.asked[0][2])
        # What is set is sent, what is set to nothing is not, and the model stays the one configured.
        self.assertEqual(self.server.asked[1][2], {"model": "made-up-speaker", "input": "Lit.", "voice": "ada-plain",
                                                   "response_format": "wav", "speed": 0.95})

    @needs_ffmpeg
    def test_sound_that_is_not_a_wav_file_is_made_into_one(self):
        self.server.answer = (200, self.sound("flac"))
        target = SpeechServer(settings=self.settings()).say("Lit.", self.folder / "line.wav", voice=ADA)
        self.assertEqual(target.read_bytes()[:4], b"RIFF")
        self.assertAlmostEqual(ffmpeg.probe(target).duration_s, 0.3, delta=0.02)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["line.wav", "tone.flac"])

    @needs_ffmpeg
    def test_an_answer_that_is_not_sound_is_refused(self):
        speaker = SpeechServer(settings=self.settings())
        self.server.answer = (200, b'{"error": "this is a made-up server and it has no voice to give you today"}' * 3)
        self.refused(lambda: speaker.say("Lit.", self.folder / "line.wav", voice=ADA),
                     f"the speech model at {self.url} answered with something that is not sound")
        self.assertEqual(list(self.folder.iterdir()), [])
        self.server.answer = (200, b"")
        self.refused(lambda: speaker.say("Lit.", self.folder / "line.wav", voice=ADA), "answered with nothing to hear")

    def test_a_key_is_sent_and_never_shown(self):
        speaker = SpeechServer(settings=self.settings(CONSTRUCTIVE_MEDIA_KEY_SPEECH="made-up-key"))
        self.server.answer = (401, {"error": "no"})
        said = self.refused(lambda: speaker.say("Lit.", self.folder / "line.wav", voice=ADA),
                            f"the speech model at {self.url} refused with 401")
        self.assertNotIn("made-up-key", said)
        self.assertEqual(self.server.asked[0][1]["Authorization"], "Bearer made-up-key")

    def test_a_voice_the_server_cannot_know_is_not_sent(self):
        speaker = SpeechServer(settings=self.settings())
        self.refused(lambda: speaker.say("Lit.", self.folder / "line.wav", voice=Voice("ada", "warm")),
                     "the warm voice of 'ada' has no profile")
        self.assertEqual(self.server.asked, [])

    def test_nothing_is_sent_anywhere_it_was_not_told_to_go(self):
        nothing = Settings.from_env({})
        self.refused(lambda: SpeechServer(settings=nothing), "nothing says where the speech model is",
                     "CONSTRUCTIVE_MEDIA_URL_SPEECH", "audio/speech")
        self.refused(lambda: SpeechServer(settings=Settings.from_env({"CONSTRUCTIVE_MEDIA_URL_SPEECH": self.url})),
                     "nothing says which speech model to ask for", "CONSTRUCTIVE_MEDIA_MODEL_SPEECH")
        self.refused(lambda: SpeechServer(settings=self.settings(CONSTRUCTIVE_MEDIA_EXTRA_SPEECH="[1]")),
                     "CONSTRUCTIVE_MEDIA_EXTRA_SPEECH must be a JSON object")


if __name__ == "__main__":
    unittest.main()
