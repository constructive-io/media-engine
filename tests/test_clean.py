"""Cleaning a recording of a voice: what is measured, what is advised, and what is done.

The recordings are written here: made-up talk in phrases, with a made-up
noise under it. No model cleans anything; a stand-in does, where one is asked for.
"""

import tempfile
import unittest
from pathlib import Path

from constructive_media import adapters
from constructive_media.denoise import Denoiser, denoiser
from constructive_media.denoise.ffmpeg import FfmpegDenoiser

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy, syllables
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import clean, pcm

RATE = 48_000


def phrases(noise=None, *, quiet=0.8):
    """Three phrases with quiet after each, and `noise(time)` under all of it."""
    hush = np.zeros(int(quiet * RATE), dtype=np.float32)
    talk = np.concatenate([part for seed in (1, 2, 3) for part in (syllables(2.0, RATE, seed=seed), hush)])
    if noise is not None:
        talk = talk + noise(np.arange(len(talk)) / RATE).astype(np.float32)
    return talk


def hiss(level=0.002):
    return lambda time: level * np.random.default_rng(7).standard_normal(len(time))


def rumble(time):
    return 0.02 * np.sin(2 * np.pi * 38 * time) + 1e-5 * np.random.default_rng(7).standard_normal(len(time))


def hum(time):
    return (sum(0.004 / n * np.sin(2 * np.pi * 60 * n * time) for n in (1, 2, 3))
            + 2e-5 * np.random.default_rng(7).standard_normal(len(time)))


class Scrubs:
    """A stand-in for a denoiser: it halves everything, ten times over, wherever the recording is quiet."""

    name = "stand-in"

    def __init__(self):
        self.asked = []

    def clean(self, source, target, *, floor=None):
        self.asked.append(floor)
        heard = pcm.read(source, stereo=False)
        size = int(pcm.rate(source) * 0.02)
        for at in range(0, len(heard) - size + 1, size):
            if np.abs(heard[at:at + size]).max() < 0.02:
                heard[at:at + size] *= 0.001
        return pcm.write(Path(target), heard, pcm.rate(source))


@needs_numpy
@needs_ffmpeg
class CleanTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def recording(self, noise=None, name="recording.wav", **more):
        return pcm.write(self.folder / name, phrases(noise, **more), RATE, bits=24)

    def test_a_clean_recording_is_left_alone_and_nothing_is_written(self):
        noise = clean.measure(self.recording())
        self.assertAlmostEqual(noise.seconds, 8.4, delta=0.01)
        self.assertGreater(noise.snr, clean.CLEAN_DB)
        steps, (why,) = clean.advise(noise)
        self.assertEqual(steps, [])
        self.assertIn("leave it alone", why)
        told = clean.clean(self.recording(), self.folder / "cleaned.wav")
        self.assertEqual((told["output"], told["steps"]), (None, []))
        self.assertFalse((self.folder / "cleaned.wav").exists())

    def test_rumble_goes_to_a_filter_and_never_to_a_denoiser(self):
        noise = clean.measure(self.recording(rumble))
        self.assertLess(noise.snr, clean.CLEAN_DB)
        self.assertGreater(noise.rumble, noise.low + 20)
        steps, why = clean.advise(noise)
        self.assertEqual(steps, ["highpass"])
        self.assertIn("rumble", why[0])
        told = clean.clean(self.recording(rumble), self.folder / "cleaned.flac")
        self.assertEqual((told["steps"], told["denoiser"]), (["highpass"], None))
        self.assertGreater(told["gained"], 15)
        self.assertEqual((told["damaged"], told["worth_it"]), (False, True))
        self.assertEqual(pcm.shape(self.folder / "cleaned.flac"), (1, RATE))

    def test_a_hum_at_the_mains_is_heard_and_notched(self):
        noise = clean.measure(self.recording(hum))
        self.assertEqual(noise.hum, 60)
        steps, why = clean.advise(noise)
        self.assertIn("notch", steps)
        self.assertTrue(any("hums at 60 Hz" in said for said in why))
        told = clean.clean(self.recording(hum), self.folder / "cleaned.wav", steps=["notch"])
        self.assertEqual(told["hum"], 60)
        self.assertGreater(told["gained"], 10)
        self.assertIsNone(clean.measure(self.folder / "cleaned.wav").hum)
        self.assertEqual(clean.filters(["notch"], hum=50)[0], "bandreject=f=50:width_type=q:w=30")
        with self.assertRaisesRegex(clean.CleanError, "50 or 60"):
            clean.filters(["notch"])

    def test_hiss_goes_to_the_denoiser_which_is_told_how_loud_it_is(self):
        noise = clean.measure(self.recording(hiss()))
        steps, why = clean.advise(noise)
        self.assertEqual(steps, ["denoise"])
        self.assertIn("it hisses", why[0])
        self.assertIn("listen to what it gives back", why[0])
        scrubs = Scrubs()
        told = clean.clean(self.recording(hiss()), self.folder / "cleaned.wav", denoiser=scrubs)
        self.assertEqual((told["steps"], told["denoiser"]), (["denoise"], "stand-in"))
        self.assertAlmostEqual(scrubs.asked[0], noise.floor, delta=0.1)
        self.assertGreater(told["gained"], 20)
        self.assertFalse(told["damaged"])

    def test_a_cleaning_that_costs_the_voice_is_called_damage(self):
        class Halves(Scrubs):
            def clean(self, source, target, *, floor=None):
                heard = pcm.read(source, stereo=False)
                loud = np.abs(heard) > 0.02
                heard[loud] *= 0.5
                return pcm.write(Path(target), heard, pcm.rate(source))

        told = clean.clean(self.recording(hiss()), self.folder / "cleaned.wav", denoiser=Halves())
        self.assertGreater(told["voice_lost"], clean.DAMAGE_DB)
        self.assertEqual((told["damaged"], told["worth_it"]), (True, False))

    def test_a_recording_with_no_quiet_in_it_is_not_noise_to_take_off(self):
        steady = lambda time: 0.05 * np.sin(2 * np.pi * 220 * time)          # noqa: E731 - something playing under it
        noise = clean.measure(self.recording(steady))
        self.assertIsNone(noise.floor)
        steps, (why,) = clean.advise(noise)
        self.assertEqual(steps, [])
        self.assertIn("no quiet in it", why)
        self.assertIn("separation", why)

    def test_what_cannot_be_measured_or_done_is_refused(self):
        with self.assertRaisesRegex(clean.CleanError, "no such file"):
            clean.measure(self.folder / "gone.wav")
        short = pcm.write(self.folder / "short.wav", syllables(0.1, RATE), RATE)
        with self.assertRaisesRegex(clean.CleanError, "too short"):
            clean.measure(short)
        with self.assertRaisesRegex(clean.CleanError, "polish is not a way to clean"):
            clean.clean(self.recording(hiss()), self.folder / "cleaned.wav", steps=["polish"])

    def test_ffmpeg_s_own_denoiser_takes_a_steady_hiss_down(self):
        told = clean.clean(self.recording(hiss()), self.folder / "cleaned.wav", denoiser=FfmpegDenoiser())
        self.assertEqual(told["denoiser"], "ffmpeg")
        self.assertGreater(told["gained"], clean.GAIN_DB)
        self.assertFalse(told["damaged"])

    def test_it_is_measured_and_cleaned_from_the_command_line(self):
        recording = self.recording(rumble)
        status, out, _ = run("clean", str(recording))
        self.assertEqual(status, 0)
        self.assertIn("8.4 seconds, the voice at", out)
        self.assertIn("rumble", out)
        self.assertIn(f"to do it: me clean {recording}", out)
        status, out, _ = run("clean", str(recording), str(self.folder / "cleaned.wav"))
        self.assertEqual(status, 0)
        self.assertIn(f"{self.folder / 'cleaned.wav'}: highpass", out)
        self.assertIn("dB further under the voice", out)
        status, out, _ = run("clean", str(self.recording(name="fine.wav")), str(self.folder / "fine-cleaned.wav"))
        self.assertEqual(status, 0)
        self.assertIn("nothing was written", out)


class SlotTest(unittest.TestCase):
    def test_the_slot_is_filled_by_ffmpeg_until_something_better_is_chosen(self):
        self.assertIn("denoise", adapters.SLOTS)
        self.assertIsInstance(denoiser("ffmpeg"), Denoiser)
        self.assertEqual(denoiser().name, "ffmpeg")

    def test_the_floor_it_is_told_is_kept_to_what_the_filter_takes(self):
        self.assertEqual(FfmpegDenoiser().filter(), "afftdn=nr=12:nf=-50.0:tn=1")
        self.assertEqual(FfmpegDenoiser().filter(-63.21), "afftdn=nr=12:nf=-63.2:tn=1")
        self.assertEqual(FfmpegDenoiser().filter(-120.0), "afftdn=nr=12:nf=-80.0:tn=1")
        self.assertEqual(FfmpegDenoiser().filter(-3.0), "afftdn=nr=12:nf=-20.0:tn=1")


class CommandTest(unittest.TestCase):
    def test_a_recording_is_only_cleaned_when_there_is_somewhere_to_write_it(self):
        status, _, err = run("clean", "recording.wav", "--steps", "highpass")
        self.assertEqual(status, 2)
        self.assertIn("give the recording to write", err)
        status, _, err = run("clean", "no-such-recording.wav")
        self.assertEqual(status, 1)
        self.assertIn("no such file", err)


if __name__ == "__main__":
    unittest.main()
