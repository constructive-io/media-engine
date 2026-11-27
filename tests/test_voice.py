"""A voice, from a recording to a line.

The recordings are written here, of made-up talk that says nothing. No model
speaks or listens: a stand-in writes the takes and another says what it heard.
"""

import base64
import tempfile
import unittest
from pathlib import Path

from constructive_media.settings import Settings
from constructive_media.speech import GivenVoices, SpeechError, Voice
from constructive_media.speech.http import SpeechServer, filled
from constructive_media.transcription import Segment

from .support import HAVE_FFMPEG, HAVE_NUMPY, Listener, Speaker, make_tone, needs_ffmpeg, needs_numpy, syllables
from .test_cli import run
from .test_text import Served

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import check, pcm, voice

SAID = "The tide tables are kept in the drawer under the lamp."
ADA = Voice("ada", profile="ada-plain")


class Heard:
    """A stand-in for a speech-to-text model that hears the same thing in everything."""

    def __init__(self, text):
        self.text, self.listened = text, []

    def transcribe(self, audio, *, language="en", prompt=None):
        self.listened.append(Path(audio))
        return [Segment(0.0, 1.0, self.text)]


class FilledTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.clip = Path(folder.name) / "ada.flac"
        self.clip.write_bytes(b"not really sound")
        self.ada = Voice("ada", "warm", profile="ada-plain", reference_audio=str(self.clip), reference_text=SAID)

    def test_what_a_value_names_of_the_voice_is_put_in(self):
        asked = {"ref_audio": "{reference:base64}", "ref_path": "{reference}", "ref_text": "{said}",
                 "who": "{persona} when {emotion}", "speed": 1.1, "seed": None, "plain": "as it is"}
        self.assertEqual(filled(asked, self.ada), {
            "ref_audio": base64.b64encode(b"not really sound").decode(), "ref_path": str(self.clip.resolve()),
            "ref_text": SAID, "who": "ada when warm", "speed": 1.1, "seed": None, "plain": "as it is"})

    def test_it_is_put_in_however_deep_it_is(self):
        asked = {"voice": {"clips": [{"audio": "{reference}", "text": "{said}"}], "id": None}}
        self.assertEqual(filled(asked, self.ada),
                         {"voice": {"clips": [{"audio": str(self.clip.resolve()), "text": SAID}], "id": None}})

    def test_what_names_something_the_voice_lacks_is_left_out(self):
        asked = {"ref_audio": "{reference:base64}", "ref_text": "{said}", "who": "{persona}", "clips": ["{reference}"]}
        self.assertEqual(filled(asked, ADA), {"who": "ada", "clips": []})

    def test_braces_that_name_nothing_are_left_alone(self):
        self.assertEqual(filled({"prompt": "say {this} as {written}"}, ADA), {"prompt": "say {this} as {written}"})

    def test_a_recording_that_has_gone_is_told(self):
        gone = Voice("ada", profile="ada-plain", reference_audio=str(self.clip.with_name("gone.flac")))
        with self.assertRaises(SpeechError) as raised:
            filled({"ref_audio": "{reference:base64}"}, gone)
        self.assertIn("is not there", str(raised.exception))
        self.assertIn("gone.flac", str(raised.exception))


class ReferenceSentTest(Served):
    @needs_ffmpeg
    def test_the_recording_and_its_words_go_with_every_line_when_the_setting_asks(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        clip = make_tone(Path(folder.name) / "ada.wav", 0.3)
        self.server.answer = (200, clip.read_bytes())
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_URL_SPEECH": self.url, "CONSTRUCTIVE_MEDIA_MODEL_SPEECH": "made-up-speaker",
            "CONSTRUCTIVE_MEDIA_EXTRA_SPEECH": '{"voice": null, "ref_audio": "{reference:base64}", "ref_text": "{said}"}'})
        speaker = SpeechServer(settings=settings)
        cloned = Voice("ada", profile="ada-plain", reference_audio=str(clip), reference_text=SAID)
        speaker.say("The lantern is lit.", Path(folder.name) / "line.wav", voice=cloned, seed=3)
        speaker.say("The lantern is lit.", Path(folder.name) / "line.wav", voice=ADA, seed=3)
        (_, _, with_clip), (_, _, without) = self.server.asked
        self.assertEqual(with_clip, {"model": "made-up-speaker", "input": "The lantern is lit.",
                                     "response_format": "wav", "seed": 3, "ref_text": SAID,
                                     "ref_audio": base64.b64encode(clip.read_bytes()).decode()})
        self.assertEqual(without, {"model": "made-up-speaker", "input": "The lantern is lit.",
                                   "response_format": "wav", "seed": 3})


@needs_numpy
@needs_ffmpeg
class ReferenceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def recording(self, seconds, *, quiet=1.0, rate=48_000, name="recording.wav"):
        """Talk with quiet before and after it, in two channels."""
        hush = np.zeros(int(quiet * rate), dtype=np.float32)
        talk = np.concatenate([hush, syllables(seconds, rate), hush])
        return pcm.write(self.folder / name, np.stack([talk, talk], axis=1), rate, bits=16)

    def test_a_recording_is_measured(self):
        measured = voice.measure(self.recording(20.0))
        self.assertEqual((measured["seconds"], measured["channels"], measured["rate"]), (22.0, 2, 48_000))
        self.assertLess(measured["loudness"], -10)
        self.assertLess(measured["peak"], 0)
        self.assertEqual(voice.fit(measured), ([], ["it has 2 channels; a clip to clone from has one"]))

    def test_what_is_wrong_with_a_recording_is_said(self):
        def fit(seconds, **more):
            return voice.fit({"seconds": seconds, "channels": 1, "rate": 24_000, "loudness": -19.0, **more})

        self.assertEqual(fit(20.0), ([], []))
        (stop,), notes = fit(41.0)
        self.assertIn("41.0 seconds", stop)
        self.assertIn("--from and --to", stop)
        self.assertEqual(notes, [])
        self.assertIn("too little of a voice", fit(8.0)[1][0])
        self.assertIn("15 to 28 is what clones best", fit(13.0)[1][0])
        self.assertIn("15 to 28 is what clones best", fit(29.0)[1][0])
        self.assertIn("16000 Hz", fit(20.0, rate=16_000)[1][0])
        self.assertIn("nothing to hear", fit(20.0, loudness=None)[0][0])

    def test_a_recording_that_is_not_there_or_holds_nothing_is_refused(self):
        with self.assertRaises(SpeechError) as raised:
            voice.measure(self.folder / "gone.wav")
        self.assertIn("no such file", str(raised.exception))

    def test_a_clip_is_cut_to_the_shape_a_voice_is_cloned_from(self):
        told = voice.reference(self.recording(20.0), self.folder / "voices" / "ada" / "neutral.flac", said=SAID)
        clip = self.folder / "voices" / "ada" / "neutral.flac"
        self.assertEqual(told["output"], str(clip))
        self.assertEqual(pcm.shape(clip), (1, 24_000))
        # The second of quiet at either end is gone, all but a breath of it.
        self.assertAlmostEqual(told["is"]["seconds"], 20.3, delta=0.4)
        self.assertAlmostEqual(told["is"]["loudness"], voice.LOUDNESS, delta=0.6)
        self.assertLessEqual(told["is"]["peak"], voice.TRUE_PEAK + 0.3)
        self.assertEqual(told["from"]["seconds"], 22.0)
        self.assertEqual((told["said"], told["heard"], told["notes"]), (SAID, False, []))
        self.assertEqual(clip.with_suffix(".txt").read_text(), SAID + "\n")

    def test_a_part_of_a_long_recording_is_taken(self):
        long = self.recording(50.0)
        with self.assertRaises(SpeechError) as raised:
            voice.reference(long, self.folder / "all.flac")
        self.assertIn("cannot be a clip to clone from", str(raised.exception))
        self.assertIn("past 30", str(raised.exception))
        self.assertFalse((self.folder / "all.flac").exists())
        told = voice.reference(long, self.folder / "part.wav", start=5.0, end=25.0)
        self.assertAlmostEqual(told["is"]["seconds"], 20.0, delta=0.3)
        self.assertEqual((told["said"], told["said_file"]), (None, None))
        self.assertFalse((self.folder / "part.txt").exists())
        with self.assertRaises(SpeechError) as raised:
            voice.reference(long, self.folder / "part.wav", start=9.0, end=4.0)
        self.assertIn("--to must be later", str(raised.exception))

    def test_the_words_are_heard_in_the_clip_as_cut_and_a_short_clip_is_remarked_on(self):
        listens = Heard(" The tide tables are kept. ")
        clip = self.folder / "short.wav"
        told = voice.reference(self.recording(9.0), clip, transcriber=listens)
        self.assertEqual(listens.listened, [clip])
        self.assertEqual((told["said"], told["heard"]), ("The tide tables are kept.", True))
        self.assertEqual(clip.with_suffix(".txt").read_text(), "The tide tables are kept.\n")
        self.assertIn("too little of a voice", told["notes"][0])
        # Words that are given are not listened for.
        told = voice.reference(self.recording(9.0), clip, said=SAID, transcriber=listens)
        self.assertEqual((len(listens.listened), told["said"], told["heard"]), (1, SAID, False))


@needs_numpy
@needs_ffmpeg
class SayTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.voices = GivenVoices([ADA, Voice("ada", "whisper", profile="ada-quiet"),
                                   Voice("grace", profile="grace-plain", reference_text=SAID)])

    def stand_ins(self, wrong=None, hears=None):
        speaker = Speaker(wrong)
        return speaker, {"voices": self.voices, "synthesizer": lambda engine: speaker,
                         "transcriber": Listener(speaker, hears)}

    def test_words_are_said_in_the_voice_named_and_written_where_asked(self):
        speaker, how = self.stand_ins()
        told = voice.say("The lantern is lit.", self.folder / "out" / "one.wav", persona="ada", **how)
        self.assertEqual({key: told[key] for key in ("persona", "emotion", "profile", "seed", "attempts", "status")},
                         {"persona": "ada", "emotion": "neutral", "profile": "ada-plain", "seed": voice.SEED,
                          "attempts": 1, "status": "clean"})
        self.assertEqual(pcm.shape(self.folder / "out" / "one.wav"), (1, 24_000))
        self.assertEqual([(text, said.profile, seed) for text, said, seed in speaker.asked],
                         [("The lantern is lit.", "ada-plain", voice.SEED)])
        self.assertEqual(sorted(path.name for path in (self.folder / "out").iterdir()), ["one.wav"])

    def test_the_emotion_the_seed_and_the_kind_of_file_are_as_asked(self):
        speaker, how = self.stand_ins()
        told = voice.say("Quietly now.", self.folder / "two.flac", persona="ada", emotion="whisper", seed=9, **how)
        self.assertEqual((told["emotion"], told["profile"], told["seed"]), ("whisper", "ada-quiet", 9))
        self.assertGreater(pcm.seconds(self.folder / "two.flac"), 1.0)
        self.assertEqual((self.folder / "two.flac").read_bytes()[:4], b"fLaC")

    def test_a_take_that_fails_is_said_again(self):
        speaker, how = self.stand_ins(hears=lambda text, seed: "ba ko mi ru te" if seed == voice.SEED else text)
        told = voice.say("The lantern is lit at dusk.", self.folder / "three.wav", persona="ada", **how)
        self.assertEqual((told["attempts"], told["status"]), (2, "healed"))

    def test_nobody_and_nothing_are_refused(self):
        _, how = self.stand_ins()
        with self.assertRaises(SpeechError) as raised:
            voice.say("Hello.", self.folder / "x.wav", persona="nobody", **how)
        self.assertIn("nobody", str(raised.exception))
        with self.assertRaises(SpeechError) as raised:
            voice.say("  ", self.folder / "x.wav", persona="ada", **how)
        self.assertIn("no words", str(raised.exception))

    def test_an_audition_says_every_line_once_and_counts_what_passed(self):
        speaker, how = self.stand_ins()
        told = voice.audition("ada", self.folder / "audition", **how)
        self.assertEqual((told["good"], told["of"], told["bled"], told["passed"]), (8, 8, [], True))
        self.assertEqual(len(speaker.asked), 8)
        self.assertEqual(len({seed for _, _, seed in speaker.asked}), 8)
        self.assertEqual(sorted(path.name for path in (self.folder / "audition").iterdir()),
                         [f"line_{number:02d}.wav" for number in range(1, 9)])
        self.assertEqual(told["lines"][4]["text"], "Wait.")

    def test_a_voice_that_says_the_end_of_its_clip_first_fails_its_audition(self):
        said = []
        speaker, how = self.stand_ins(hears=lambda text, seed: ("the lamp " if text.startswith("Could") else "") + text)
        told = voice.audition("grace", self.folder / "audition", tell=said.append, **how)
        self.assertEqual((told["good"], told["of"], told["bled"], told["passed"]), (7, 8, [2], False))
        self.assertEqual((told["lines"][1]["reason"], told["lines"][1]["bled"]), (check.REFERENCE_BLEED, "the lamp"))
        self.assertIn("began with `the lamp`", said[1])
        self.assertEqual(said[0], "  [01] ok")
        self.assertEqual(len(speaker.asked), 8)   # no second try

    def test_a_voice_that_garbles_too_much_fails_and_lines_can_be_given(self):
        lines = ["One line about the harbour wall.", "", "Another about the tide.", "A third about the lamp."]
        speaker, how = self.stand_ins(hears=lambda text, seed: "ba ko mi ru te" if "tide" in text else text)
        told = voice.audition("ada", self.folder / "audition", lines=lines, **how)
        self.assertEqual((told["good"], told["of"], told["bled"], told["passed"]), (2, 3, [], False))
        with self.assertRaises(SpeechError):
            voice.audition("ada", self.folder / "audition", lines=[" "], **how)


class CommandsTest(unittest.TestCase):
    def test_the_voice_commands_are_registered(self):
        out = run("--help")[1]
        for command in ("reference", "audition"):
            self.assertIn(command, out)
        self.assertIn("--voice", run("say", "--help")[1])

    def test_what_belongs_to_a_script_is_refused_with_a_voice_and_the_other_way_round(self):
        status, _, err = run("say", "--voice", "ada", "Some words.", "out.wav", "--stems")
        self.assertEqual(status, 2)
        self.assertIn("--stems is for a script", err)
        status, _, err = run("say", "scene.conv", "out.wav", "--emotion", "warm")
        self.assertEqual(status, 2)
        self.assertIn("go with --voice", err)

    def test_a_recording_is_only_cut_when_there_is_somewhere_to_write_the_clip(self):
        status, _, err = run("reference", "recording.wav", "--from", "3")
        self.assertEqual(status, 2)
        self.assertIn("give the clip to write", err)
        status, _, err = run("reference", "no-such-recording.wav")
        self.assertEqual(status, 1)
        self.assertIn("no such file", err)

    @unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
    def test_a_recording_is_measured_and_cut_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            recording = pcm.write(Path(folder) / "recording.wav", syllables(18.0, 24_000), 24_000, bits=16)
            status, out, _ = run("reference", str(recording))
            self.assertEqual(status, 0)
            self.assertIn("18.0 seconds, 1 channel at 24000 Hz", out)
            self.assertIn("fit to clone a voice from", out)
            clip = Path(folder) / "ada.flac"
            status, out, _ = run("reference", str(recording), str(clip), "--said", SAID)
            self.assertEqual(status, 0)
            self.assertIn(f"its words are in {clip.with_suffix('.txt')}", out)
            self.assertEqual(clip.with_suffix(".txt").read_text(), SAID + "\n")
            long = pcm.write(Path(folder) / "long.wav", syllables(40.0, 24_000), 24_000, bits=16)
            status, out, _ = run("reference", str(long))
            self.assertEqual(status, 1)
            self.assertIn("cut 15 to 28 seconds of it", out)

    def test_an_audition_needs_its_lines_to_be_there(self):
        status, _, err = run("audition", "ada", "--lines", "no-such-lines.txt")
        self.assertEqual(status, 2)
        self.assertIn("no such file", err)
