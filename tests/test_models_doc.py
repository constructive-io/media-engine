"""The model list says what the code does.

Every model an adapter knows by name is in docs/MODELS.md with its license
and the page it is published at, and nothing is named there that no adapter
knows. Only the licenses the rule allows are known by name at all.
"""

import re
import unittest
from pathlib import Path

from constructive_media.detection import onnx as detection
from constructive_media.speakers import onnx as speakers
from constructive_media.transcription import whispercpp as transcription

PAGE = (Path(__file__).resolve().parent.parent / "docs" / "MODELS.md").read_text(encoding="utf-8")
ROW = re.compile(r"^\| `([^`]+)`(?: \(default\))? \| (.+?) \| (\S+) \| \[[^\]]+\]\((\S+)\) \|$", re.MULTILINE)
ALLOWED = ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause")


def known():
    """Every model known by name: its name, what it is, its license and where its file is."""
    for name, model in transcription.KNOWN.items():
        yield name, model.summary, model.license, model.file.url
    for name, model in detection.KNOWN.items():
        yield name, model.summary, model.license, model.files[detection.WEIGHTS].url
    for name, model in speakers.KNOWN.items():
        yield name, model.summary, model.license, model.weights.url


class ModelList(unittest.TestCase):
    def test_every_model_known_by_name_is_listed_as_it_is_defined(self):
        listed = {name: (summary, license, page) for name, summary, license, page in ROW.findall(PAGE)}
        for name, summary, license, url in known():
            with self.subTest(model=name):
                self.assertIn(name, listed)
                self.assertEqual(listed[name], (summary, license, url.split("/resolve/")[0]))
        self.assertEqual(sorted(listed), sorted(name for name, *_ in known()))

    def test_the_defaults_are_marked(self):
        for module in (transcription, detection, speakers):
            self.assertIn(f"| `{module.DEFAULT_MODEL}` (default) |", PAGE)
        self.assertEqual(PAGE.count("(default) |"), 3)

    def test_only_licenses_the_rule_allows_are_known_by_name(self):
        for name, _, license, url in known():
            with self.subTest(model=name):
                self.assertIn(license, ALLOWED)
                # Pinned to one revision, never to whatever a branch holds today.
                self.assertRegex(url, r"/resolve/[0-9a-f]{40}/")

    def test_the_model_that_finds_speech_is_listed(self):
        self.assertIn(f"`{transcription.SPEECH_MODEL}`", PAGE)
        self.assertIn(transcription.SPEECH_REMOTE.url.split("/resolve/")[0], PAGE)


if __name__ == "__main__":
    unittest.main()
