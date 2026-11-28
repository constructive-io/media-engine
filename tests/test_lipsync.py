"""A face in a video made to say a recording.

Nothing here moves a mouth. The videos are flat colours and the speech is a
tone, and a stand-in for the model writes the video again for as long as the
speech runs, with a higher tone of its own under it. So what can be checked
is everything around the model: what it is handed, how long the result
runs, whose sound is under the picture, and what is said about it.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, ffmpeg, lipsync

from .support import HAVE_NUMPY, make_tone, make_video, needs_ffmpeg, needs_numpy
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm

#: The pitch of the speech, which is what `make_tone` writes, and of the sound the stand-in writes.
SPEECH, ITS_OWN = 440, 1000


class Puppet:
    """A stand-in for a model that moves a mouth: the video, for as long as the speech, with a sound of its own."""

    name, model = "puppet", "made-up-puppet"

    def __init__(self, wrong=None):
        self.wrong, self.asked = wrong, []

    def sync(self, video, speech, target):
        self.asked.append((Path(video).name, Path(speech).name, Path(target).suffix))
        if isinstance(self.wrong, Exception):
            raise self.wrong
        if self.wrong == "nothing":
            return target
        if self.wrong == "no answer":
            return None
        if self.wrong == "sound":
            return make_tone(Path(target).with_suffix(".wav"))
        seconds = ffmpeg.probe(speech).duration_s * (0.5 if self.wrong == "short" else 1)
        smaller = ["-vf", "scale=80:60"] if self.wrong == "small" else []
        ffmpeg.encode(
            "-stream_loop", "-1", "-i", video, "-f", "lavfi", "-i", f"sine=frequency={ITS_OWN}:duration={seconds}",
            "-map", "0:v:0", "-map", "1:a:0", *smaller, "-c:v", "{vcodec}", "-b:v", "1M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", f"{seconds:.3f}", target,
        )
        return target


def plugged(puppet):
    """A registry in which a plugin fills the slot with `puppet`."""
    registry = adapters.Registry()
    registry.register(adapters.Adapter(slot="lip_sync", name="puppet", factory=lambda: puppet,
                                       license="LicenseRef-made-up", summary="made up", origin="a-plugin"))
    return registry


class SlotTest(unittest.TestCase):
    def test_it_is_a_slot(self):
        self.assertIn("lip_sync", adapters.SLOTS)

    def test_nothing_built_in_fills_the_slot(self):
        self.assertEqual([one.name for one in adapters.builtins() if one.slot == "lip_sync"], [])

    def test_asking_with_nothing_installed_says_what_to_install(self):
        with self.assertRaisesRegex(adapters.AdapterError, "lip_sync.*Install a plugin"):
            adapters.Registry().get("lip_sync")

    def test_a_plugin_fills_it(self):
        with mock.patch("constructive_media.adapters._registry", plugged(Puppet())):
            found = lipsync.syncer("puppet")
        self.assertIsInstance(found, lipsync.Syncer)

    def test_a_plugin_is_not_used_until_it_is_chosen(self):
        with self.assertRaisesRegex(adapters.AdapterError, "Choose one of: puppet"):
            plugged(Puppet()).get("lip_sync")


@needs_ffmpeg
class SyncTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def face(self, seconds=3.0, name="face.mp4"):
        return make_video(self.folder / name, colours=("0xd0a080",), seconds=seconds)

    def line(self, seconds=1.0, name="line.wav"):
        return make_tone(self.folder / name, seconds)

    def test_it_is_written_where_it_was_asked_for_and_runs_as_long_as_the_speech(self):
        told = lipsync.sync(self.face(3.0), self.line(1.0), self.folder / "out" / "said.mp4", syncer=Puppet())
        written = ffmpeg.probe(self.folder / "out" / "said.mp4")
        self.assertEqual((written.medium, written.width, written.height), ("video", 160, 90))
        self.assertTrue(written.has_audio)
        self.assertAlmostEqual(written.duration_s, 1.0, delta=0.15)
        self.assertEqual(told["output"], str(self.folder / "out" / "said.mp4"))
        self.assertAlmostEqual(told["seconds"], 1.0, delta=0.15)
        self.assertEqual((told["video_seconds"], told["speech_seconds"]), (3.0, 1.0))
        self.assertEqual((told["size"], told["fps"]), ([160, 90], 25.0))
        self.assertEqual(told["notes"], [])

    @needs_numpy
    def test_the_sound_under_the_picture_is_the_speech_handed_in_and_not_the_adapters(self):
        target = self.folder / "said.mp4"
        lipsync.sync(self.face(), self.line(1.0), target, syncer=Puppet())
        sound = pcm.read(target, stereo=False)
        loudest = np.fft.rfftfreq(len(sound), 1 / pcm.rate(target))[np.abs(np.fft.rfft(sound)).argmax()]
        self.assertAlmostEqual(loudest, SPEECH, delta=5)
        self.assertEqual((pcm.channels(target), pcm.rate(target)), (2, 48_000))

    def test_it_says_what_did_it_and_how_long_it_took(self):
        puppet = Puppet()
        told = lipsync.sync(self.face(), self.line(), self.folder / "said.mp4", syncer=puppet)
        self.assertEqual((told["video"], told["speech"], told["adapter"], told["model"]),
                         ("face.mp4", "line.wav", "puppet", "made-up-puppet"))
        self.assertGreaterEqual(told["took"], 0)
        self.assertEqual(puppet.asked, [("face.mp4", "line.wav", ".mp4")])

    def test_a_video_that_runs_out_before_the_speech_is_said_to(self):
        told = lipsync.sync(self.face(1.0), self.line(2.0), self.folder / "said.mp4", syncer=Puppet())
        self.assertAlmostEqual(told["seconds"], 2.0, delta=0.15)
        self.assertEqual(len(told["notes"]), 1)
        self.assertIn("the video ran out 1.0 s before the speech did", told["notes"][0])

    def test_a_picture_that_ends_before_the_speech_is_said_to(self):
        told = lipsync.sync(self.face(3.0), self.line(2.0), self.folder / "said.mp4", syncer=Puppet("short"))
        self.assertEqual(told["notes"], ["the picture that came back ends 1.0 s before the speech does"])
        self.assertAlmostEqual(told["seconds"], 2.0, delta=0.15)

    def test_a_picture_that_came_back_another_size_is_said_to(self):
        told = lipsync.sync(self.face(), self.line(), self.folder / "said.mp4", syncer=Puppet("small"))
        self.assertEqual(told["notes"], ["the picture came back at 80x60, where the video was 160x90"])
        self.assertEqual(told["size"], [80, 60])

    def test_the_speech_may_be_the_sound_of_another_video(self):
        other = make_video(self.folder / "other.mp4", colours=("blue",), seconds=1.0)
        told = lipsync.sync(self.face(), other, self.folder / "said.mov", syncer=Puppet())
        self.assertEqual(told["speech"], "other.mp4")
        self.assertTrue(ffmpeg.probe(self.folder / "said.mov").has_audio)

    def test_a_picture_the_file_cannot_hold_as_it_is_is_written_again(self):
        real, copied = ffmpeg.run, []

        def run_but_not_copy(program, *arguments, **how):
            if "copy" in arguments:
                copied.append(arguments)
                raise ffmpeg.FfmpegError("ffmpeg failed: could not write header")
            return real(program, *arguments, **how)

        with mock.patch.object(ffmpeg, "run", run_but_not_copy):
            told = lipsync.sync(self.face(), self.line(), self.folder / "said.mp4", syncer=Puppet())
        self.assertEqual(len(copied), 1)
        self.assertAlmostEqual(told["seconds"], 1.0, delta=0.15)
        self.assertEqual(told["size"], [160, 90])

    def test_what_cannot_be_used_is_refused_before_the_model_is_asked(self):
        puppet, face, line = Puppet(), self.face(), self.line()
        silent = self.folder / "silent.mp4"
        ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", face, "-an", "-c:v", "copy", silent)
        for video, speech, output, said in (
            (self.folder / "missing.mp4", line, "said.mp4", "no such video"),
            (face, self.folder / "missing.wav", "said.mp4", "no such recording"),
            (line, line, "said.mp4", "holds no picture"),
            (face, silent, "said.mp4", "holds no sound"),
            (face, line, "said.wav", "the name says what kind of file"),
            (face, line, "said", "the name says what kind of file"),
            (face, line, "face.mp4", "is what was handed in"),
        ):
            with self.subTest(said=said), self.assertRaisesRegex(lipsync.LipSyncError, said):
                lipsync.sync(video, speech, self.folder / output, syncer=puppet)
        self.assertEqual(puppet.asked, [])
        self.assertFalse((self.folder / "said.mp4").exists())

    def test_an_adapter_that_wrote_no_picture_is_refused_and_nothing_is_written(self):
        for wrong in ("nothing", "no answer", "sound"):
            with self.subTest(wrong=wrong), \
                    self.assertRaisesRegex(lipsync.LipSyncError, "puppet adapter wrote no picture"):
                lipsync.sync(self.face(), self.line(), self.folder / "out" / "said.mp4", syncer=Puppet(wrong))
        self.assertFalse((self.folder / "out").exists())

    def test_what_the_adapter_could_not_do_is_said_as_it_said_it(self):
        puppet = Puppet(lipsync.LipSyncError("no face was found in face.mp4"))
        with self.assertRaisesRegex(lipsync.LipSyncError, "no face was found in face.mp4"):
            lipsync.sync(self.face(), self.line(), self.folder / "said.mp4", syncer=puppet)
        self.assertFalse((self.folder / "said.mp4").exists())


@needs_ffmpeg
class CommandTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.face = make_video(self.folder / "face.mp4", colours=("0xd0a080",), seconds=1.0)
        self.line = make_tone(self.folder / "line.wav", 2.0)
        self.said = self.folder / "said.mp4"
        patched = mock.patch("constructive_media.adapters._registry", plugged(Puppet()))
        patched.start()
        self.addCleanup(patched.stop)

    def test_it_prints_what_was_written_and_what_to_look_at(self):
        status, out, err = run("lipsync", str(self.face), str(self.line), str(self.said), "--adapter", "puppet")
        self.assertEqual((status, err), (0, ""))
        first, second, note = out.splitlines()
        self.assertRegex(first, rf"^{self.said}: \d\.\d s at 160x90, face\.mp4 saying line\.wav$")
        self.assertRegex(second, r"^  2\.0 s of speech over 1\.0 s of video, in \d+\.\d s by puppet made-up-puppet$")
        self.assertIn("the video ran out 1.0 s before the speech did", note)

    def test_the_whole_report_as_json(self):
        status, out, _ = run("lipsync", str(self.face), str(self.line), str(self.said), "--adapter", "puppet", "--json")
        told = json.loads(out)
        self.assertEqual(status, 0)
        self.assertEqual(sorted(told), ["adapter", "fps", "model", "notes", "output", "seconds", "size", "speech",
                                        "speech_seconds", "took", "video", "video_seconds"])

    def test_the_adapter_is_the_one_the_settings_choose(self):
        with mock.patch.dict("os.environ", {"CONSTRUCTIVE_MEDIA_ADAPTER_LIP_SYNC": "puppet"}):
            status, _, err = run("lipsync", str(self.face), str(self.line), str(self.said))
        self.assertEqual((status, err), (0, ""))

    def test_with_none_chosen_it_says_which_there_are(self):
        with mock.patch.dict("os.environ"):
            os.environ.pop("CONSTRUCTIVE_MEDIA_ADAPTER_LIP_SYNC", None)
            status, out, err = run("lipsync", str(self.face), str(self.line), str(self.said))
        self.assertEqual((status, out), (1, ""))
        self.assertIn("Choose one of: puppet", err)

    def test_what_is_refused_is_said_and_nothing_is_written(self):
        status, out, err = run("lipsync", str(self.folder / "missing.mp4"), str(self.line), str(self.said),
                               "--adapter", "puppet")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("no such video", err)
        self.assertFalse(self.said.exists())


class NothingInstalledTest(unittest.TestCase):
    @needs_ffmpeg
    def test_the_command_says_a_plugin_is_needed(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("constructive_media.adapters._registry", adapters.Registry()), \
                mock.patch.dict("os.environ"):
            os.environ.pop("CONSTRUCTIVE_MEDIA_ADAPTER_LIP_SYNC", None)
            face = make_video(Path(folder) / "face.mp4", colours=("0xd0a080",), seconds=1.0)
            line = make_tone(Path(folder) / "line.wav")
            status, out, err = run("lipsync", str(face), str(line), str(Path(folder) / "said.mp4"))
        self.assertEqual((status, out), (1, ""))
        self.assertIn("nothing fills the 'lip_sync' slot", err)
        self.assertIn("Install a plugin", err)


if __name__ == "__main__":
    unittest.main()
