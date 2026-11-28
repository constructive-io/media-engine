"""Sounds made by the moss adapter, with a stand-in for the interpreter that runs the model.

No model is loaded and nothing here needs Apple silicon: the adapter is
pointed at a script that keeps what it was given and writes a made-up file.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media.generate import GenerateError, Request
from constructive_media.generate import moss as foley

from .support import settings_in

WORDS = "a made-up bell across still water"

PYTHON = f"""#!{sys.executable}
import json, sys
from pathlib import Path

given = sys.argv[1:]
Path(__file__).with_name("asked.json").write_text(json.dumps(given))
asked = json.loads(given[-1])
if "cracked" in asked["prompt"]:
    print("loading the made-up model")
    print("Traceback: something long", file=sys.stderr)
    print(json.dumps({{"error": "ValueError: the made-up bell is cracked"}}))
    sys.exit(1)
if "silence" in asked["prompt"]:
    print("warming up", file=sys.stderr)
    print("the made-up model fell over", file=sys.stderr)
    sys.exit(4)
if "nothing" not in asked["prompt"]:
    Path(asked["out"]).write_bytes(b"made-up sound of " + asked["prompt"].encode())
    print(json.dumps({{"out": asked["out"]}}))
"""


class MossTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.python = self.folder / "bin" / "python"
        self.python.parent.mkdir()
        self.python.write_text(PYTHON)
        self.python.chmod(0o755)
        self.target = self.folder / "work" / "bell.wav"

    def maker(self, models=None, **options):
        return foley.Moss(settings=settings_in(
            self.folder / "home", models=models or {},
            extras={"audio": json.dumps({"python": str(self.python), **options})},
        ))

    def asked(self):
        given = json.loads((self.python.parent / "asked.json").read_text())
        return given[:-1], json.loads(given[-1])

    def test_a_sound_is_made_with_the_defaults(self):
        made = self.maker().make(Request("audio", WORDS), self.target)
        self.assertEqual((made, made.read_bytes()), (self.target, b"made-up sound of a made-up bell across still water"))
        ran, asked = self.asked()
        self.assertEqual(ran, ["-P", str(foley.RUNNER)])
        out = asked.pop("out")
        self.assertEqual(asked, {"prompt": WORDS, "seconds": 4.0, "seed": foley.seed_of(WORDS),
                                 "model": foley.DEFAULT_MODEL, "codec": foley.DEFAULT_CODEC})
        # Written somewhere of its own, moved into place, and nothing left behind.
        self.assertNotEqual(Path(out), self.target)
        self.assertEqual(sorted(one.name for one in self.target.parent.iterdir()), ["bell.wav"])
        self.assertTrue((self.folder / "home" / "locks" / "mlx.lock").is_file())

    def test_the_same_words_are_given_the_same_seed_and_other_words_another(self):
        self.assertEqual(foley.seed_of(WORDS), foley.seed_of("  a made-up bell\nacross still   water "))
        self.assertNotEqual(foley.seed_of(WORDS), foley.seed_of(WORDS + ", twice"))
        self.assertTrue(0 <= foley.seed_of(WORDS) < 2 ** 31)

    def test_what_is_asked_for_is_what_the_runner_is_told(self):
        self.maker().make(Request("audio", WORDS, seconds=9.5, seed=7), self.target)
        _, asked = self.asked()
        self.assertEqual((asked["seconds"], asked["seed"]), (9.5, 7))

    def test_the_model_and_the_options_come_from_settings(self):
        maker = self.maker({"audio": "made-up/bells"}, seconds=6, codec="made-up/codec")
        self.assertEqual((maker.name, maker.model), ("moss", "made-up/bells"))
        maker.make(Request("audio", WORDS), self.target)
        _, asked = self.asked()
        self.assertEqual((asked["model"], asked["codec"], asked["seconds"]), ("made-up/bells", "made-up/codec", 6.0))

    def test_a_sound_is_written_over_the_one_it_replaces(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"the first")
        self.maker().make(Request("audio", WORDS), self.target)
        self.assertEqual(self.target.read_bytes(), b"made-up sound of a made-up bell across still water")

    def test_a_runner_that_fails_says_why_in_one_line(self):
        for words, said in (
            ("a bell, cracked", "ValueError: the made-up bell is cracked"),
            ("silence", "the made-up model fell over"),
            ("nothing", "it gave no reason"),
        ):
            with self.subTest(words), self.assertRaises(GenerateError) as raised:
                self.maker().make(Request("audio", words), self.target)
            self.assertEqual(str(raised.exception), f"the moss adapter could not make the sound: {said}")
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_what_cannot_be_made_is_refused_before_anything_is_run(self):
        maker = self.maker()
        for request, said in (
            (Request("image", WORDS), "makes sounds, not image"),
            (Request("audio", WORDS, seconds=301), "a sound of 301 seconds cannot be made: the longest is 300"),
        ):
            with self.subTest(said), self.assertRaisesRegex(GenerateError, said):
                maker.make(request, self.target)
        self.assertFalse((self.python.parent / "asked.json").exists())

    def test_options_it_does_not_know_are_refused(self):
        with self.assertRaisesRegex(GenerateError, "has reverb, which the moss adapter does not know; it knows: python"):
            self.maker(reverb=2)
        for written, said in (("{", "is not JSON"), ("[4]", "should be a JSON object")):
            with self.assertRaisesRegex(GenerateError, said):
                foley.Moss(settings=settings_in(self.folder / "home", extras={"audio": written}))

    def test_the_interpreter_is_the_one_named_or_the_engines_own_when_it_has_the_library(self):
        plain = foley.Moss(settings=settings_in(self.folder / "home"))
        with mock.patch.object(foley.importlib.util, "find_spec", lambda name: object()):
            self.assertEqual(plain.interpreter(), [sys.executable])
            command = plain.command(Request("audio", WORDS), self.target)
            self.assertEqual(command[:-1], [sys.executable, "-P", str(foley.RUNNER)])
        with mock.patch.object(foley.importlib.util, "find_spec", lambda name: None):
            with self.assertRaisesRegex(GenerateError, "runs mlx-speech, which is not installed here.*say where"):
                plain.check(Request("audio", WORDS))
        # One that is named is the one that is run, and it must be there.
        self.assertEqual(self.maker().interpreter(), [str(self.python)])
        with self.assertRaisesRegex(GenerateError, "there is no Python at "):
            self.maker(python=str(self.folder / "nowhere" / "python")).check(Request("audio", WORDS))

    def test_the_runner_is_a_program_of_its_own(self):
        """It is run by another interpreter, which need not have the engine: so it imports none of it."""
        self.assertTrue(foley.RUNNER.is_file())
        tree = ast.parse(foley.RUNNER.read_text(encoding="utf-8"))
        named = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        named |= {node.module or "." for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse([name for name in named if name.startswith(("constructive_media", "."))])
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level])


if __name__ == "__main__":
    unittest.main()
