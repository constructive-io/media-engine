import importlib.util
import tempfile
import unittest
from pathlib import Path

from constructive_media import adapters, detection
from constructive_media.detection import Detection, Detector
from constructive_media.detection.torchvision import DEFAULT_MODEL, TorchvisionDetector

from .support import HAVE_FFMPEG, make_still, settings_in

HAVE_TORCH = all(importlib.util.find_spec(name) for name in ("torch", "torchvision"))
SMALL_MODEL = "fasterrcnn_mobilenet_v3_large_fpn"


class DetectionTest(unittest.TestCase):
    def test_corners_become_a_box_measured_against_the_frame(self):
        found = Detection.from_corners("person", 0.9, 100, 50, 300, 250, width=400, height=500)
        self.assertEqual((found.cx, found.cy, found.w, found.h), (0.5, 0.3, 0.5, 0.4))
        self.assertAlmostEqual(found.area, 0.2)

    def test_a_box_that_spills_over_the_frame_is_clipped_to_it(self):
        found = Detection.from_corners("person", 0.5, -20, -10, 500, 600, width=400, height=500)
        self.assertEqual((found.cx, found.cy, found.w, found.h), (0.5, 0.5, 1.0, 1.0))


class TorchvisionAdapterTest(unittest.TestCase):
    """What can be said of the adapter without loading a model."""

    def test_it_is_built_in_and_bsd_but_not_the_default(self):
        described = adapters.registry().describe("detection", "torchvision")
        self.assertEqual((described.origin, described.license), ("builtin", "BSD-3-Clause"))
        self.assertNotEqual(adapters.registry().describe("detection").name, "torchvision")

    def test_it_is_a_detector_before_anything_heavy_is_imported(self):
        finder = TorchvisionDetector(settings=settings_in("/nowhere"))
        self.assertIsInstance(finder, Detector)
        self.assertEqual((finder.name, finder.model, finder.device), ("torchvision", DEFAULT_MODEL, "auto"))

    def test_the_model_and_device_come_from_settings(self):
        settings = settings_in("/nowhere", device="cpu", models={"detection": SMALL_MODEL})
        finder = TorchvisionDetector(settings=settings)
        self.assertEqual((finder.model, finder.device), (SMALL_MODEL, "cpu"))
        self.assertEqual(TorchvisionDetector("other", settings=settings).model, "other")

    def test_it_is_what_detector_returns_when_settings_choose_it(self):
        settings = settings_in("/nowhere", adapters={"detection": "torchvision"})
        self.assertIsInstance(detection.detector(settings=settings), TorchvisionDetector)
        self.assertIsInstance(detection.detector("torchvision", settings=settings_in("/nowhere")),
                              TorchvisionDetector)


@unittest.skipUnless(HAVE_TORCH and HAVE_FFMPEG, "torch, torchvision and ffmpeg are needed")
class UntrainedModelTest(unittest.TestCase):
    """The adapter end to end, on a model with no weights to download."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(cls.folder.name)
        cls.images = [make_still(root / "wide.png"), make_still(root / "tall.jpg", size="90x160")]
        cls.finder = TorchvisionDetector(SMALL_MODEL, "cpu", pretrained=False)

    def test_every_image_gets_an_answer_in_order(self):
        found = self.finder.detect(self.images, confidence=0.0)
        self.assertEqual(len(found), 2)
        labels = set(self.finder._labels)
        self.assertIn("person", labels)
        for detections in found:
            for d in detections:
                self.assertIn(d.label, labels)
                for share in (d.cx, d.cy, d.w, d.h, d.confidence):
                    self.assertTrue(0.0 <= share <= 1.0, d)

    def test_nothing_clears_a_bar_set_above_certainty(self):
        self.assertEqual(self.finder.detect(self.images, confidence=1.1), [[], []])
        self.assertEqual(self.finder.detect([]), [])

    def test_an_unknown_model_is_named_in_the_error(self):
        with self.assertRaises(ValueError) as raised:
            TorchvisionDetector("no_such_model", "cpu", pretrained=False).detect(self.images)
        self.assertIn("no_such_model", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
