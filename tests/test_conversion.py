"""What one voice said, said again in another.

Nothing here converts a voice. A stand-in writes the tones of the clip it
is given for as long as the recording it is given lasts, which is what a
conversion is: one recording's length and another's sound. A stand-in for
the speaker model hears how loud each tone is, so how alike two recordings
are can be worked out by hand.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, conversion
from constructive_media.speech import GivenVoices, SpeechError, Voice

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy
from .test_cli import run
from .test_talk import Answers, Hears, Says

if HAVE_NUMPY:
    from constructive_media.audio import pcm, voice

    from .test_mimic import Ear, tones


class Turner:
    """A stand-in for a model that converts a voice: the clip's sound, for as long as the recording lasts."""

    name, model = "turner", "made-up-turner"

    def __init__(self, wrong=None):
        self.wrong, self.asked = wrong, []

    def convert(self, source, target, *, like):
        self.asked.append((Path(source).name, Path(like).name))
        if self.wrong == "nothing":
            return Path(target)
        if self.wrong is not None:
            raise self.wrong
        row = Ear().embed([pcm.read(like, rate=16_000, stereo=False)])[0]
        return pcm.write(target, tones(row, seconds=pcm.seconds(source)), 24_000)


class Marks:
    """A stand-in for a converter that needs no sound: it writes down what it was handed."""

    name, model = "marks", "made-up-marks"

    def convert(self, source, target, *, like):
        Path(target).write_text(f"{Path(source).read_text()} as {Path(like).name}")
        return Path(target)


class Writes:
    """A stand-in for a speech model that writes the words down as they are."""

    name, model = "writes", "made-up-voice"

    def __init__(self):
        self.asked = []

    def say(self, text, target, *, voice, seed=None):
        self.asked.append((text, voice, seed))
        Path(target).write_text(text)
        return Path(target)


class SlotTest(unittest.TestCase):
    def test_nothing_built_in_fills_the_slot(self):
        self.assertEqual([one.name for one in adapters.builtins() if one.slot == "voice_conversion"], [])

    def test_asking_with_nothing_installed_says_what_to_install(self):
        with self.assertRaisesRegex(adapters.AdapterError, "voice_conversion.*Install a plugin"):
            adapters.Registry().get("voice_conversion")

    def test_a_plugin_fills_it(self):
        registry = adapters.Registry()
        registry.register(adapters.Adapter(slot="voice_conversion", name="turner", factory=Turner,
                                           license="GPL-3.0-only", summary="made up", origin="a-plugin"))
        with mock.patch("constructive_media.adapters._registry", registry):
            found = conversion.converter("turner")
        self.assertIsInstance(found, conversion.Converter)

    def test_what_goes_wrong_is_a_failure_to_speak(self):
        self.assertTrue(issubclass(conversion.ConversionError, SpeechError))


class RespokenTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.clip = self.folder / "ada.wav"
        self.clip.write_text("a clip")
        self.ada = Voice("ada", engine="mlx", reference_audio=str(self.clip), reference_text="Said in it.")
        self.first = Writes()
        self.speaks = conversion.Respoken(self.first, Voice("quick", profile="af_made_up"), Marks())

    def test_words_are_said_in_the_quick_voice_and_again_in_the_one_asked_for(self):
        said = self.speaks.say("The tide is out.", self.folder / "line.wav", voice=self.ada, seed=7)
        self.assertEqual(said, self.folder / "line.wav")
        self.assertEqual(said.read_text(), "The tide is out. as ada.wav")
        self.assertEqual([(text, one.profile, seed) for text, one, seed in self.first.asked],
                         [("The tide is out.", "af_made_up", 7)])

    def test_it_is_named_for_both_its_parts(self):
        self.assertEqual((self.speaks.name, self.speaks.model), ("writes+marks", "made-up-voice + made-up-marks"))

    def test_a_voice_with_no_clip_cannot_be_said_again_in(self):
        with self.assertRaisesRegex(conversion.ConversionError, "'ada' was not made from a clip"):
            self.speaks.say("The tide is out.", self.folder / "line.wav", voice=Voice("ada", profile="af_made_up"))
        self.assertEqual(self.first.asked, [])

    def test_a_clip_that_has_gone_is_said_to_be_gone(self):
        self.clip.unlink()
        with self.assertRaisesRegex(conversion.ConversionError, "is not there: .*ada.wav"):
            self.speaks.say("The tide is out.", self.folder / "line.wav", voice=self.ada)


@needs_numpy
class RevoiceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.take = pcm.write(self.folder / "take.wav", tones([1.0, 0.0], seconds=2.0), 24_000)
        self.clip = pcm.write(self.folder / "ada.wav", tones([0.0, 1.0]), 24_000)
        self.turner = Turner()
        self.voices = GivenVoices([Voice("ada", engine="mlx", reference_audio=str(self.clip)),
                                   Voice("grace", profile="a-voice-of-the-servers")])

    def revoice(self, target="out.wav", **how):
        how.setdefault("converter", self.turner)
        return voice.revoice(self.take, self.folder / target, embedder=Ear(), voices=self.voices, **how)

    def test_a_recording_is_said_again_like_a_clip(self):
        told = self.revoice(like=self.clip)
        self.assertEqual(self.turner.asked, [("take.wav", "ada.wav")])
        self.assertEqual((told["recording"], told["like"], told["adapter"], told["model"]),
                         ("take.wav", "ada.wav", "turner", "made-up-turner"))
        self.assertEqual(told["output"], str(self.folder / "out.wav"))
        # It lasts as long as what was said, and sounds like the clip where the recording did not.
        self.assertAlmostEqual(told["seconds"], 2.0, places=1)
        self.assertAlmostEqual(pcm.seconds(self.folder / "out.wav"), 2.0, places=1)
        self.assertLess(told["before"], 0.1)
        self.assertGreater(told["alike"], 0.95)
        self.assertGreaterEqual(told["took"], 0)

    def test_or_in_the_voice_of_a_persona_that_was_cloned_from_a_clip(self):
        told = self.revoice(who="Ada")
        self.assertEqual(told["like"], "ada")
        self.assertEqual(self.turner.asked, [("take.wav", "ada.wav")])

    def test_a_voice_that_is_only_a_name_has_nothing_to_be_heard_by(self):
        with self.assertRaisesRegex(SpeechError, "'grace' was not made from a clip"):
            self.revoice(who="grace")
        self.assertEqual(self.turner.asked, [])

    def test_it_is_one_voice_or_one_clip(self):
        for how in ({}, {"who": "ada", "like": self.clip}):
            with self.subTest(how=how), self.assertRaisesRegex(SpeechError, "give one of them"):
                self.revoice(**how)

    def test_what_is_not_there_is_said(self):
        with self.assertRaisesRegex(SpeechError, "nowhere.wav: no such clip"):
            self.revoice(like=self.folder / "nowhere.wav")
        with self.assertRaisesRegex(SpeechError, "gone.wav: no such recording"):
            voice.revoice(self.folder / "gone.wav", self.folder / "out.wav", like=self.clip, converter=self.turner,
                          embedder=Ear())

    def test_a_converter_that_wrote_nothing_is_not_taken_at_its_word(self):
        with self.assertRaisesRegex(conversion.ConversionError, "the turner adapter wrote nothing for take.wav"):
            self.revoice(like=self.clip, converter=Turner("nothing"))
        self.assertFalse((self.folder / "out.wav").exists())

    def test_what_a_converter_says_went_wrong_is_passed_on(self):
        with self.assertRaisesRegex(conversion.ConversionError, "the model is not there"):
            self.revoice(like=self.clip, converter=Turner(conversion.ConversionError("the model is not there")))

    @needs_ffmpeg
    def test_the_name_says_what_kind_of_file(self):
        told = self.revoice("deep/out.flac", like=self.clip)
        self.assertEqual(told["output"], str(self.folder / "deep" / "out.flac"))
        self.assertEqual((self.folder / "deep" / "out.flac").read_bytes()[:4], b"fLaC")


class CommandTest(unittest.TestCase):
    def test_the_command_is_there_and_says_what_it_takes(self):
        status, out, _ = run("revoice", "--help")
        self.assertEqual(status, 0)
        for said in ("--voice", "--like", "--emotion", "--json", "voice_conversion", "plugin"):
            self.assertIn(said, out)

    def test_whose_voice_has_to_be_said(self):
        for arguments, said in (
            ((), "say whose voice"),
            (("--voice", "ada", "--like", "ada.wav"), "say whose voice"),
            (("--like", "ada.wav", "--emotion", "warm"), "--emotion goes with --voice"),
        ):
            with self.subTest(arguments=arguments):
                status, _, err = run("revoice", "take.wav", "out.wav", *arguments)
                self.assertEqual(status, 2)
                self.assertIn(said, err)

    @needs_numpy
    def test_it_prints_how_like_the_voice_it_was_and_is(self):
        with tempfile.TemporaryDirectory() as folder:
            take = pcm.write(Path(folder) / "take.wav", tones([1.0, 0.0], seconds=2.0), 24_000)
            clip = pcm.write(Path(folder) / "ada.wav", tones([0.0, 1.0]), 24_000)
            with mock.patch("constructive_media.conversion.converter", return_value=Turner()), \
                    mock.patch("constructive_media.speakers.embedder", return_value=Ear()):
                status, out, _ = run("revoice", str(take), str(Path(folder) / "out.wav"), "--like", str(clip))
                self.assertEqual(status, 0, out)
                self.assertRegex(out, r"out\.wav: 1\.00 alike ada\.wav, where take\.wav was 0\.0\d\n"
                                      r"  2\.0 s said again in \d+\.\d s by turner made-up-turner\n$")
                status, out, _ = run("revoice", str(take), str(Path(folder) / "out.wav"), "--like", str(clip),
                                     "--json")
                self.assertEqual(json.loads(out)["like"], "ada.wav")
                # Said again like itself, it is no more like the voice than it was, and that is said.
                status, out, _ = run("revoice", str(clip), str(Path(folder) / "same.wav"), "--like", str(clip))
                self.assertIn("it is no more like that voice than it was", out)

    def test_with_nothing_to_convert_with_it_says_what_to_install(self):
        with tempfile.TemporaryDirectory() as folder:
            take = Path(folder) / "take.wav"
            take.write_bytes(b"RIFF")
            with mock.patch("constructive_media.adapters._registry", adapters.Registry()):
                status, _, err = run("revoice", str(take), str(Path(folder) / "out.wav"), "--like", str(take))
        self.assertEqual(status, 1)
        self.assertRegex(err, r"^media: .*nothing fills the 'voice_conversion' slot.*Install a plugin")


class TalkTest(unittest.TestCase):
    """`media talk --quick`: a sentence said in a quick voice and again in a persona's."""

    def run_with(self, voices, *arguments, turner=None):
        self.first = Says()
        with mock.patch("constructive_media.text.writer", return_value=Answers(["Rivers. More"], ["Rivers. More"])), \
                mock.patch("constructive_media.speech.synthesizer", return_value=self.first) as self.synthesizer, \
                mock.patch("constructive_media.transcription.transcriber", return_value=Hears("Why is the sea salty?")), \
                mock.patch("constructive_media.speech.StoredVoices", return_value=voices), \
                mock.patch("constructive_media.conversion.converter", return_value=turner or Passes()):
            return run("talk", *arguments)

    def test_a_quick_voice_goes_with_a_persona(self):
        status, _, err = run("talk", "--quick", "af_made_up")
        self.assertEqual(status, 2)
        self.assertIn("--quick goes with --voice", err)

    def test_the_answer_is_said_quickly_and_again_in_the_persona_s_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "ada.wav"
            clip.write_bytes(b"RIFF")
            voices = GivenVoices([Voice("ada", engine="mlx", reference_audio=str(clip))])
            status, out, _ = self.run_with(voices, "--voice", "Ada", "--quick", "af_made_up", "--measure", "--typed")
        self.assertEqual(status, 0, out)
        self.assertIn("stand-in+passes", out)
        self.assertIn("made-up-voice + made-up-passes", out)
        # Two names end to end are longer than one, and the columns still line up.
        heading, saying = (next(line for line in out.splitlines() if word in line) for word in ("first time", "saying"))
        self.assertEqual(heading.index("model"), saying.index("made-up-voice"))
        self.assertEqual(len(heading), len(saying))
        # The configured adapter says it, in the quick voice, and not the one the persona's voice names.
        self.assertEqual(self.synthesizer.call_args.args, ())
        self.assertEqual({one.profile for _, one in self.first.said}, {"af_made_up"})

    def test_a_persona_with_no_clip_is_found_out_before_anything_is_said(self):
        voices = GivenVoices([Voice("grace", profile="a-voice-of-the-servers")])
        status, out, err = self.run_with(voices, "--voice", "grace", "--quick", "af_made_up", "--measure", "--typed")
        self.assertEqual((status, out), (1, ""))
        self.assertRegex(err, r"^media: .*'grace' was not made from a clip")
        self.assertEqual(self.first.said, [])


class Passes:
    """A stand-in for a converter that hands on what it was given."""

    name, model = "passes", "made-up-passes"

    def convert(self, source, target, *, like):
        Path(target).write_bytes(Path(source).read_bytes())
        return Path(target)


if __name__ == "__main__":
    unittest.main()
