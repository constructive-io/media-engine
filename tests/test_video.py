import tempfile
import unittest
from unittest import mock
from pathlib import Path

from .support import HAVE_NUMPY, make_video, needs_ffmpeg, needs_numpy

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.video import cuts, pixels


def flat(red, green, blue, size=(8, 12)):
    return np.full((*size, 3), (red, green, blue), dtype=np.uint8)


@needs_numpy
class PixelsTest(unittest.TestCase):
    def test_hue_saturation_value_of_the_primaries(self):
        for colour, hue in (((255, 0, 0), 0), ((0, 255, 0), 120), ((0, 0, 255), 240), ((255, 255, 0), 60)):
            h, s, v = pixels.hsv(flat(*colour))
            self.assertAlmostEqual(float(h[0, 0]), hue, places=3)
            self.assertAlmostEqual(float(s[0, 0]), 1.0)
            self.assertAlmostEqual(float(v[0, 0]), 1.0)

    def test_grey_has_no_hue_and_no_saturation(self):
        h, s, v = pixels.hsv(flat(128, 128, 128))
        self.assertEqual((float(h[0, 0]), float(s[0, 0])), (0.0, 0.0))
        self.assertAlmostEqual(float(v[0, 0]), 128 / 255, places=5)
        self.assertEqual(float(pixels.hsv(flat(0, 0, 0))[1][0, 0]), 0.0)

    def test_luma_weighs_green_most(self):
        red, green, blue = (float(pixels.luma(flat(*c))[0, 0]) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255)))
        self.assertGreater(green, red)
        self.assertGreater(red, blue)
        self.assertAlmostEqual(float(pixels.luma(flat(255, 255, 255))[0, 0]), 255.0, places=3)

    def test_sharpness_is_nothing_for_a_flat_image_and_high_for_detail(self):
        blank = np.full((32, 32), 90, dtype=np.uint8)
        checks = (np.indices((32, 32)).sum(axis=0) % 2 * 255).astype(np.uint8)
        self.assertEqual(pixels.sharpness(blank), 0.0)
        self.assertGreater(pixels.sharpness(checks), 1000)

    def test_clipped_counts_crushed_and_blown_pixels(self):
        gray = np.array([[0, 2, 128, 128], [128, 128, 253, 255]], dtype=np.uint8)
        self.assertEqual(pixels.clipped(gray), 0.5)

    def test_bytes_become_an_image_of_the_right_shape(self):
        self.assertEqual(pixels.image(bytes(2 * 3 * 3), 3, 2).shape, (2, 3, 3))
        self.assertEqual(pixels.image(bytes(2 * 3), 3, 2, channels=1).shape, (2, 3))


@needs_numpy
class CutsTest(unittest.TestCase):
    RED, GREEN, PALE = (200, 30, 30), (30, 90, 30), (220, 220, 250)

    def frames(self, *runs):
        return [flat(*colour) for colour, length in runs for _ in range(length)]

    def test_scores_come_out_in_order_however_the_work_is_split(self):
        rng = np.random.default_rng(7)
        frames = [rng.integers(0, 256, (18, 32, 3), dtype=np.uint8) for _ in range(70)]
        whole = list(cuts.scores(frames))
        self.assertEqual((len(whole), whole[0]), (70, 0.0))
        self.assertTrue(all(score > 0 for score in whole[1:]))
        for chunk, workers in ((1, 1), (7, 3), (200, 2)):
            with mock.patch.multiple(cuts, CHUNK=chunk, WORKERS=workers):
                self.assertEqual(list(cuts.scores(iter(frames))), whole)

    def test_a_change_of_colour_is_a_cut(self):
        found, count = cuts.cuts(self.frames((self.RED, 20), (self.GREEN, 20), (self.PALE, 20)),
                                 min_frames=10)
        self.assertEqual((found, count), ([20, 40], 60))

    def test_a_steady_picture_has_no_cuts(self):
        self.assertEqual(cuts.cuts(self.frames((self.RED, 30)), min_frames=10), ([], 30))
        self.assertEqual(cuts.cuts([], min_frames=10), ([], 0))

    def test_a_slow_drift_stays_under_the_threshold(self):
        drift = [flat(60 + step, 40, 40) for step in range(100)]
        self.assertEqual(cuts.cuts(drift, min_frames=10)[0], [])

    def test_a_flash_is_one_cut_not_two(self):
        white = (250, 250, 250)
        found, _ = cuts.cuts(self.frames((self.RED, 20), (white, 2), (self.RED, 20)), min_frames=10)
        self.assertEqual(found, [20])

    def test_hue_alone_weighs_least(self):
        same_brightness = self.frames(((200, 30, 30), 20), ((30, 200, 30), 20))
        self.assertEqual(cuts.cuts(same_brightness, min_frames=10)[0], [])
        self.assertEqual(cuts.cuts(same_brightness, threshold=15, min_frames=10)[0], [20])

    def test_a_strobe_is_one_stretch_and_the_next_shot_starts_where_it_ends(self):
        strobe = [(self.RED, 1), (self.PALE, 1)] * 10
        found, _ = cuts.cuts(self.frames((self.RED, 20), *strobe, (self.GREEN, 20)), min_frames=10)
        self.assertEqual(found, [21, 40])

    def test_nothing_cuts_before_the_first_shot_is_long_enough(self):
        found, _ = cuts.cuts(self.frames((self.RED, 4), (self.GREEN, 30)), min_frames=10)
        self.assertEqual(found, [])

    def test_the_threshold_decides(self):
        frames = self.frames(((100, 100, 100), 20), ((130, 130, 130), 20))
        self.assertEqual(cuts.cuts(frames, threshold=5, min_frames=10)[0], [20])
        self.assertEqual(cuts.cuts(frames, threshold=27, min_frames=10)[0], [])

    @needs_ffmpeg
    def test_shots_are_found_in_a_real_file(self):
        with tempfile.TemporaryDirectory() as folder:
            video = make_video(Path(folder, "three.mp4"))
            found = cuts.shots(video)
            self.assertEqual(len(found), 3)
            self.assertEqual(found[0][0], 0.0)
            for (start, end), expected in zip(found, ((0, 1), (1, 2), (2, 3))):
                self.assertAlmostEqual(start, expected[0], delta=0.05)
                self.assertAlmostEqual(end, expected[1], delta=0.1)
            self.assertEqual(cuts.shots(Path(folder, "absent.mp4")), [])


if __name__ == "__main__":
    unittest.main()
