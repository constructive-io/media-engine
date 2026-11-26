import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from constructive_media import adapters, detection
from constructive_media.detection import Detector
from constructive_media.detection.onnx import CONFIG, DEFAULT_MODEL, KNOWN, WEIGHTS, OnnxDetector, decode
from constructive_media.models import ModelError

from .support import needs_numpy, settings_in


def have(*names: str) -> bool:
    return all(importlib.util.find_spec(name) for name in names)


LABELS = {0: "person", 1: "dog", 2: "chair"}


class OnnxAdapterTest(unittest.TestCase):
    """What can be said of the adapter without loading a model."""

    def test_it_is_the_default_detector_and_mit(self):
        described = adapters.registry().describe("detection")
        self.assertEqual((described.name, described.license, described.origin), ("onnx", "MIT", "builtin"))
        self.assertIsInstance(detection.detector(settings=settings_in("/nowhere")), OnnxDetector)

    def test_it_is_a_detector_before_anything_heavy_is_imported(self):
        finder = OnnxDetector(settings=settings_in("/nowhere"))
        self.assertIsInstance(finder, Detector)
        self.assertEqual((finder.name, finder.model, finder.device), ("onnx", DEFAULT_MODEL, "auto"))

    def test_the_model_and_device_come_from_settings(self):
        settings = settings_in("/nowhere", device="cpu", models={"detection": "dfine_s"})
        self.assertEqual(OnnxDetector(settings=settings).model, "dfine_s")
        self.assertEqual(OnnxDetector("other", "mps", settings=settings).device, "mps")

    def test_every_known_model_is_permissive_and_pinned(self):
        self.assertIn(DEFAULT_MODEL, KNOWN)
        for name, known in KNOWN.items():
            with self.subTest(model=name):
                self.assertIn(known.license, adapters.PERMISSIVE)
                self.assertEqual(set(known.files), {WEIGHTS, CONFIG})
                for remote in known.files.values():
                    self.assertRegex(remote.url, r"^https://.+/resolve/[0-9a-f]{40}/")
                    self.assertRegex(remote.sha256, r"^[0-9a-f]{64}$")

    def test_a_model_that_is_neither_known_nor_a_folder_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ModelError) as raised:
                OnnxDetector(folder, settings=settings_in(folder)).folder()
        message = str(raised.exception)
        self.assertIn("dfine_n", message)
        self.assertTrue(re.search(r"model\.onnx and config\.json", message), message)


@needs_numpy
class DecodeTest(unittest.TestCase):
    def test_each_query_speaks_for_its_likeliest_class_surest_first(self):
        logits = [[-9.0, 0.0, -9.0], [9.0, 1.0, -9.0], [-9.0, -9.0, -9.0]]
        boxes = [[0.5, 0.5, 0.2, 0.2], [0.25, 0.5, 0.5, 1.0], [0.5, 0.5, 1.0, 1.0]]
        found = decode(logits, boxes, LABELS, 0.35)
        self.assertEqual([d.label for d in found], ["person", "dog"])
        self.assertGreater(found[0].confidence, 0.99)
        self.assertAlmostEqual(found[1].confidence, 0.5)
        self.assertEqual((found[0].cx, found[0].cy, found[0].w, found[0].h), (0.25, 0.5, 0.5, 1.0))

    def test_a_box_that_spills_over_the_frame_is_clipped_to_it(self):
        (found,) = decode([[9.0, -9.0, -9.0]], [[0.9, 0.5, 0.4, 2.0]], LABELS, 0.5)
        self.assertAlmostEqual(found.cx, 0.85)
        self.assertAlmostEqual(found.w, 0.3)
        self.assertEqual((found.cy, found.h), (0.5, 1.0))

    def test_nothing_clears_a_bar_set_above_certainty(self):
        self.assertEqual(decode([[9.0, 9.0, 9.0]], [[0.5, 0.5, 0.1, 0.1]], LABELS, 1.1), [])

    def test_a_class_the_model_never_named_keeps_its_number(self):
        (found,) = decode([[-9.0, -9.0, -9.0, 9.0]], [[0.5, 0.5, 0.1, 0.1]], LABELS, 0.5)
        self.assertEqual(found.label, "3")


def tiny_model(folder: Path, outputs=("logits", "pred_boxes")) -> Path:
    """A detector small enough to build here, in the shape real ones are exported in.

    It sees a person filling the middle of any bright image and none in a dark
    one, and is half sure of a dog in the corner of both.
    """
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper, save

    def constant(name, values):
        return numpy_helper.from_array(np.array(values, dtype=np.float32), name)

    graph = helper.make_graph(
        [
            helper.make_node("ReduceMean", ["pixel_values"], ["brightness"], axes=[1, 2, 3], keepdims=1),
            helper.make_node("Reshape", ["brightness", "per_image"], ["bright"]),
            helper.make_node("Mul", ["bright", "weight"], ["weighted"]),
            helper.make_node("Add", ["weighted", "bias"], [outputs[0]]),
            helper.make_node("Mul", ["bright", "nothing"], ["zero"]),
            helper.make_node("Add", ["zero", "where"], [outputs[1]]),
        ],
        "tiny",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["batch", 3, "height", "width"])],
        [helper.make_tensor_value_info(outputs[0], TensorProto.FLOAT, ["batch", 2, 3]),
         helper.make_tensor_value_info(outputs[1], TensorProto.FLOAT, ["batch", 2, 4])],
        [
            numpy_helper.from_array(np.array([-1, 1, 1], dtype=np.int64), "per_image"),
            constant("weight", [[[20, 0, 0], [0, 0, 0]]]),
            constant("bias", [[[-10, -10, -10], [-10, 0, -10]]]),
            constant("nothing", [[[0, 0, 0, 0], [0, 0, 0, 0]]]),
            constant("where", [[[0.5, 0.5, 0.5, 1.0], [0.9, 0.9, 0.4, 0.4]]]),
        ],
    )
    folder.mkdir(parents=True, exist_ok=True)
    save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=8),
         str(folder / WEIGHTS))
    (folder / CONFIG).write_text(json.dumps({"id2label": {str(k): v for k, v in LABELS.items()}}))
    return folder


@unittest.skipUnless(have("onnx", "onnxruntime", "numpy", "PIL"), "onnx, onnxruntime, numpy and pillow are needed")
class TinyModelTest(unittest.TestCase):
    """The adapter end to end, on a model built by the test."""

    @classmethod
    def setUpClass(cls):
        from PIL import Image

        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name)
        cls.bright, cls.dark = cls.root / "bright.png", cls.root / "dark.jpg"
        Image.new("RGB", (160, 90), "white").save(cls.bright)
        Image.new("RGB", (90, 160), "black").save(cls.dark)
        cls.finder = OnnxDetector(str(tiny_model(cls.root / "tiny")), "cpu", settings=settings_in(cls.root))

    def test_every_image_gets_an_answer_in_order(self):
        bright, dark = self.finder.detect([self.bright, self.dark])
        self.assertEqual([d.label for d in bright], ["person", "dog"])
        self.assertEqual([d.label for d in dark], ["dog"])
        person, dog = bright
        self.assertGreater(person.confidence, 0.99)
        self.assertEqual((person.cx, person.cy, person.w, person.h), (0.5, 0.5, 0.5, 1.0))
        self.assertAlmostEqual(dog.confidence, 0.5)
        self.assertAlmostEqual(dog.w, 0.3, places=5)

    def test_the_bar_decides_what_counts(self):
        self.assertEqual([[d.label for d in found] for found in self.finder.detect([self.bright], 0.9)],
                         [["person"]])
        self.assertEqual(self.finder.detect([self.bright, self.dark], 1.1), [[], []])
        self.assertEqual(self.finder.detect([]), [])

    def test_a_device_this_runtime_lacks_is_an_error_not_a_silent_fallback(self):
        import onnxruntime

        if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
            self.skipTest("this runtime has CUDA")
        finder = OnnxDetector(self.finder.model, "cuda", settings=settings_in(self.root))
        with self.assertRaises(ModelError) as raised:
            finder.detect([self.bright])
        self.assertIn("cuda", str(raised.exception))

    def test_a_model_of_another_shape_is_refused(self):
        other = tiny_model(self.root / "other", outputs=("scores", "boxes"))
        with self.assertRaises(ModelError) as raised:
            OnnxDetector(str(other), "cpu", settings=settings_in(self.root)).detect([self.bright])
        self.assertIn("logits and pred_boxes", str(raised.exception))

    def test_a_model_without_labels_is_refused(self):
        bare = tiny_model(self.root / "bare")
        (bare / CONFIG).write_text("{}")
        with self.assertRaises(ModelError) as raised:
            OnnxDetector(str(bare), "cpu", settings=settings_in(self.root)).detect([self.bright])
        self.assertIn("id2label", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
