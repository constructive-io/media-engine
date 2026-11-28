"""Sound written as a WAV file and read back."""

import tempfile
import unittest
from pathlib import Path

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm

RATE = 16_000


@needs_ffmpeg
@needs_numpy
class WavTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        moments = np.arange(RATE // 4) / RATE
        self.left = (0.5 * np.sin(2 * np.pi * 220 * moments)).astype(np.float32)
        self.right = (0.25 * np.sin(2 * np.pi * 330 * moments)).astype(np.float32)

    def test_what_is_written_is_what_is_read_at_every_width(self):
        for bits, near in ((16, 1e-4), (24, 1e-6), (32, 1e-7)):
            with self.subTest(bits):
                written = pcm.write(self.folder / f"{bits}.wav", np.stack([self.left, self.right], axis=1), RATE,
                                    bits=bits)
                self.assertEqual(pcm.shape(written), (2, RATE))
                read = pcm.read(written)
                self.assertLess(np.abs(read[:, 0] - self.left).max(), near)
                self.assertLess(np.abs(read[:, 1] - self.right).max(), near)

    def test_a_table_turned_on_its_side_is_written_all_the_same(self):
        # A row for each channel, turned to a row for each moment: the numbers are where they were in memory.
        turned = np.stack([self.left, self.right]).T
        self.assertFalse(turned.flags["C_CONTIGUOUS"])
        for bits in (16, 24, 32):
            with self.subTest(bits):
                read = pcm.read(pcm.write(self.folder / f"{bits}.wav", turned, RATE, bits=bits))
                self.assertLess(np.abs(read[:, 0] - self.left).max(), 1e-4)
                self.assertLess(np.abs(read[:, 1] - self.right).max(), 1e-4)

    def test_one_channel_is_read_as_two_alike_or_as_one(self):
        written = pcm.write(self.folder / "one.wav", self.left, RATE)
        self.assertEqual(pcm.shape(written), (1, RATE))
        both = pcm.read(written)
        self.assertEqual(both.shape, (len(self.left), 2))
        self.assertTrue(np.array_equal(both[:, 0], both[:, 1]))
        self.assertEqual(pcm.read(written, stereo=False).shape, (len(self.left),))

    def test_whole_numbers_hold_a_sample_past_full_scale_at_it_and_floats_keep_it(self):
        loud = (self.left * 4).astype(np.float32)
        self.assertAlmostEqual(float(np.abs(pcm.read(pcm.write(self.folder / "held.wav", loud, RATE))).max()), 1.0,
                               places=4)
        self.assertAlmostEqual(float(np.abs(pcm.read(pcm.write(self.folder / "kept.wav", loud, RATE, bits=32))).max()),
                               2.0, places=3)

    def test_a_width_a_wav_file_is_not_written_in_is_refused(self):
        with self.assertRaisesRegex(ValueError, "16, 24, 32 bits, not 8"):
            pcm.write(self.folder / "eight.wav", self.left, RATE, bits=8)

    def test_how_long_a_file_runs_or_none_for_what_is_not_sound(self):
        self.assertEqual(pcm.seconds(pcm.write(self.folder / "one.wav", self.left, RATE)), 0.25)
        words = self.folder / "words.txt"
        words.write_text("not sound")
        self.assertIsNone(pcm.seconds(words))
        self.assertIsNone(pcm.seconds(self.folder / "missing.wav"))


if __name__ == "__main__":
    unittest.main()
