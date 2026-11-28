"""A recording pulled apart into the voice and what was under it.

Nothing here separates anything. The recordings are two tones, a low one
for the voice and a high one for what plays under it, and a stand-in for
the model calls what is low the voice and what is high the rest. So how
loud each part should come out can be worked out by hand: a tone as tall
as 0.2 stands at -17 dB, one of 0.1 at -23 dB, and the two together at -16.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, separation

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm

RATE = 16_000
VOICE, UNDER, HIGHER = 220, 3000, 5000


def tone(pitch, tall, seconds=1.0):
    return tall * np.sin(2 * np.pi * pitch * np.arange(int(RATE * seconds)) / RATE)


class Sieve:
    """A stand-in for a model that pulls a recording apart: what is low is the voice, what is high is not."""

    name, model = "sieve", "made-up-sieve"

    def __init__(self, wrong=None, finely=False):
        self.wrong, self.finely, self.asked = wrong, finely, []

    def separate(self, source, folder):
        self.asked.append(Path(source).name)
        if isinstance(self.wrong, Exception):
            raise self.wrong
        sound = pcm.read(source, stereo=False)
        pitches = np.fft.rfftfreq(len(sound), 1 / pcm.rate(source))
        heard = np.fft.rfft(sound)

        def between(low, high, name):
            kept = np.fft.irfft(np.where((pitches >= low) & (pitches < high), heard, 0), len(sound))
            return pcm.write(Path(folder) / f"{name}.wav", kept, pcm.rate(source))

        if self.wrong == "nothing":
            return {"voice": Path(folder) / "voice.wav", "rest": between(1000, 8001, "rest")}
        if self.wrong == "unnamed":
            return {"vocals": between(0, 1000, "vocals"), "rest": between(1000, 8001, "rest")}
        if self.wrong == "alone":
            return {"voice": between(0, 1000, "voice")}
        if self.wrong == "badly named":
            return {"voice": between(0, 1000, "voice"), "../rest": between(1000, 8001, "rest")}
        if self.wrong == "a list":
            return [between(0, 1000, "voice"), between(1000, 8001, "rest")]
        if self.finely:
            return {"middle": between(1000, 4000, "middle"), "voice": between(0, 1000, "voice"),
                    "top": between(4000, 8001, "top")}
        return {"voice": between(0, 1000, "voice"), "rest": between(1000, 8001, "rest")}


class SlotTest(unittest.TestCase):
    def test_it_is_a_slot(self):
        self.assertIn("separation", adapters.SLOTS)

    def test_nothing_built_in_fills_the_slot(self):
        self.assertEqual([one.name for one in adapters.builtins() if one.slot == "separation"], [])

    def test_asking_with_nothing_installed_says_what_to_install(self):
        with self.assertRaisesRegex(adapters.AdapterError, "separation.*Install a plugin"):
            adapters.Registry().get("separation")

    def test_a_plugin_fills_it(self):
        registry = adapters.Registry()
        registry.register(adapters.Adapter(slot="separation", name="sieve", factory=Sieve,
                                           license="LicenseRef-made-up", summary="made up", origin="a-plugin"))
        with mock.patch("constructive_media.adapters._registry", registry):
            found = separation.separator("sieve")
        self.assertIsInstance(found, separation.Separator)

    def test_a_plugin_is_not_used_until_it_is_chosen(self):
        registry = adapters.Registry()
        registry.register(adapters.Adapter(slot="separation", name="sieve", factory=Sieve,
                                           license="LicenseRef-made-up", summary="made up", origin="a-plugin"))
        with self.assertRaisesRegex(adapters.AdapterError, "Choose one of: sieve"):
            registry.get("separation")


@needs_ffmpeg
@needs_numpy
class ApartTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def recording(self, *tones, name="song.wav"):
        return pcm.write(self.folder / name, sum(tones), RATE)

    def song(self):
        return self.recording(tone(VOICE, 0.2), tone(UNDER, 0.1))

    def test_the_parts_are_written_as_files_named_for_them(self):
        told = separation.apart(self.song(), self.folder / "parts", separator=Sieve())
        self.assertEqual(sorted(one.name for one in (self.folder / "parts").iterdir()), ["rest.wav", "voice.wav"])
        self.assertEqual([(one["part"], Path(one["file"]).name) for one in told["parts"]],
                         [("voice", "voice.wav"), ("rest", "rest.wav")])
        self.assertEqual(told["folder"], str(self.folder / "parts"))

    def test_each_part_is_what_the_adapter_made_of_it(self):
        told = separation.apart(self.song(), self.folder / "parts", separator=Sieve())
        voice = pcm.read(self.folder / "parts" / "voice.wav", stereo=False)
        self.assertLess(np.abs(voice - tone(VOICE, 0.2)).max(), 1e-3)
        rest = pcm.read(self.folder / "parts" / "rest.wav", stereo=False)
        self.assertLess(np.abs(rest - tone(UNDER, 0.1)).max(), 1e-3)
        self.assertEqual([one["seconds"] for one in told["parts"]], [1.0, 1.0])

    def test_it_says_how_loud_the_recording_is_and_each_part_of_it(self):
        told = separation.apart(self.song(), self.folder / "parts", separator=Sieve())
        self.assertEqual(told["level"], -16.0)
        self.assertEqual({one["part"]: one["level"] for one in told["parts"]}, {"voice": -17.0, "rest": -23.0})
        self.assertEqual(told["notes"], [])

    def test_it_says_what_did_it_and_how_long_it_took(self):
        sieve = Sieve()
        told = separation.apart(self.song(), self.folder / "parts", separator=sieve)
        self.assertEqual((told["recording"], told["adapter"], told["model"]), ("song.wav", "sieve", "made-up-sieve"))
        self.assertGreaterEqual(told["took"], 0)
        self.assertEqual(sieve.asked, ["song.wav"])

    def test_a_model_that_keeps_more_apart_has_a_file_for_each_with_the_voice_first(self):
        song = self.recording(tone(VOICE, 0.2), tone(UNDER, 0.1), tone(HIGHER, 0.05))
        told = separation.apart(song, self.folder / "parts", separator=Sieve(finely=True))
        self.assertEqual([one["part"] for one in told["parts"]], ["voice", "middle", "top"])
        self.assertEqual([one["level"] for one in told["parts"]], [-17.0, -23.0, -29.0])
        self.assertEqual(len(list((self.folder / "parts").iterdir())), 3)

    def test_the_folder_is_made_and_what_was_in_it_by_those_names_is_written_over(self):
        parts = self.folder / "deep" / "parts"
        parts.mkdir(parents=True)
        (parts / "voice.wav").write_text("an old one")
        (parts / "notes.txt").write_text("kept")
        separation.apart(self.song(), parts, separator=Sieve())
        self.assertEqual(pcm.seconds(parts / "voice.wav"), 1.0)
        self.assertEqual((parts / "notes.txt").read_text(), "kept")

    def test_a_recording_with_no_voice_in_it_is_said_to_have_none(self):
        told = separation.apart(self.recording(tone(UNDER, 0.1)), self.folder / "parts", separator=Sieve())
        (note,) = told["notes"]
        self.assertIn("no voice was found", note)

    def test_a_recording_with_nothing_under_the_voice_is_said_to_have_nothing(self):
        told = separation.apart(self.recording(tone(VOICE, 0.2)), self.folder / "parts", separator=Sieve())
        (note,) = told["notes"]
        self.assertIn("nothing was found under it", note)

    def test_a_part_that_is_only_quiet_is_not_called_empty(self):
        song = self.recording(tone(VOICE, 0.2), tone(UNDER, 0.004))                # 34 dB under the voice
        self.assertEqual(separation.apart(song, self.folder / "parts", separator=Sieve())["notes"], [])

    def test_what_is_not_a_recording_is_refused_before_the_model_is_asked(self):
        sieve = Sieve()
        with self.assertRaisesRegex(separation.SeparationError, "no such recording"):
            separation.apart(self.folder / "missing.wav", self.folder / "parts", separator=sieve)
        words = self.folder / "words.txt"
        words.write_text("not sound")
        with self.assertRaisesRegex(separation.SeparationError, "holds no sound"):
            separation.apart(words, self.folder / "parts", separator=sieve)
        with self.assertRaisesRegex(separation.SeparationError, "is a file"):
            separation.apart(self.song(), words, separator=sieve)
        self.assertEqual(sieve.asked, [])

    def test_an_adapter_that_does_not_do_what_it_is_for_is_refused(self):
        for wrong, said in (("nothing", "wrote nothing for the voice of song.wav"),
                            ("unnamed", "no part called voice"),
                            ("a list", "no part called voice"),
                            ("alone", "nothing it was apart from"),
                            ("badly named", "a name that is not one")):
            with self.subTest(wrong), self.assertRaisesRegex(separation.SeparationError, said):
                separation.apart(self.song(), self.folder / "parts", separator=Sieve(wrong))
        self.assertFalse((self.folder / "parts").exists())

    def test_what_the_adapter_could_not_do_is_said_as_it_said_it(self):
        failed = separation.SeparationError("the model is not there")
        with self.assertRaisesRegex(separation.SeparationError, "the model is not there"):
            separation.apart(self.song(), self.folder / "parts", separator=Sieve(failed))
        self.assertFalse((self.folder / "parts").exists())


@needs_ffmpeg
@needs_numpy
class CommandTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.song = pcm.write(self.folder / "song.wav", tone(VOICE, 0.2) + tone(UNDER, 0.1), RATE)
        self.sieve = Sieve()
        registry = adapters.Registry()
        registry.register(adapters.Adapter(slot="separation", name="sieve", factory=lambda: self.sieve,
                                           license="LicenseRef-made-up", summary="made up", origin="a-plugin"))
        patched = mock.patch("constructive_media.adapters._registry", registry)
        patched.start()
        self.addCleanup(patched.stop)

    def test_it_prints_each_part_and_how_loud_it_is(self):
        parts = self.folder / "parts"
        status, out, err = run("separate", str(self.song), str(parts), "--adapter", "sieve")
        self.assertEqual((status, err), (0, ""))
        first, voice, rest = out.splitlines()
        self.assertRegex(first, r"^song\.wav: -16\.0 dB, pulled apart in \d+\.\d s by sieve made-up-sieve$")
        self.assertEqual(voice.split(), ["voice", "-17.0", "dB", "1.0", "s", str(parts / "voice.wav")])
        self.assertEqual(rest.split(), ["rest", "-23.0", "dB", "1.0", "s", str(parts / "rest.wav")])

    def test_it_says_when_there_was_no_voice_to_find(self):
        pcm.write(self.song, tone(UNDER, 0.1), RATE)
        status, out, _ = run("separate", str(self.song), str(self.folder / "parts"), "--adapter", "sieve")
        self.assertEqual(status, 0)
        self.assertIn("no voice was found in the recording", out.splitlines()[-1])

    def test_the_whole_report_as_json(self):
        status, out, _ = run("separate", str(self.song), str(self.folder / "parts"), "--adapter", "sieve", "--json")
        told = json.loads(out)
        self.assertEqual(status, 0)
        self.assertEqual(sorted(told), ["adapter", "folder", "level", "model", "notes", "parts", "recording", "took"])
        self.assertEqual([sorted(one) for one in told["parts"]], [["file", "level", "part", "seconds"]] * 2)

    def test_the_adapter_is_the_one_the_settings_choose(self):
        with mock.patch.dict("os.environ", {"CONSTRUCTIVE_MEDIA_ADAPTER_SEPARATION": "sieve"}):
            status, _, err = run("separate", str(self.song), str(self.folder / "parts"))
        self.assertEqual((status, err), (0, ""))

    def test_with_none_chosen_it_says_which_there_are(self):
        with mock.patch.dict("os.environ"):
            os.environ.pop("CONSTRUCTIVE_MEDIA_ADAPTER_SEPARATION", None)
            status, out, err = run("separate", str(self.song), str(self.folder / "parts"))
        self.assertEqual((status, out), (1, ""))
        self.assertIn("Choose one of: sieve", err)

    def test_what_is_refused_is_said_and_nothing_is_written(self):
        status, out, err = run("separate", str(self.folder / "missing.wav"), str(self.folder / "parts"),
                               "--adapter", "sieve")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("no such recording", err)
        self.assertFalse((self.folder / "parts").exists())


class NothingInstalledTest(unittest.TestCase):
    @needs_ffmpeg
    def test_the_command_says_a_plugin_is_needed(self):
        from .support import make_tone

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("constructive_media.adapters._registry", adapters.Registry()), \
                mock.patch.dict("os.environ"):
            os.environ.pop("CONSTRUCTIVE_MEDIA_ADAPTER_SEPARATION", None)
            song = make_tone(Path(folder) / "song.wav")
            status, out, err = run("separate", str(song), str(Path(folder) / "parts"))
        self.assertEqual((status, out), (1, ""))
        self.assertIn("nothing fills the 'separation' slot", err)
        self.assertIn("Install a plugin", err)


if __name__ == "__main__":
    unittest.main()
