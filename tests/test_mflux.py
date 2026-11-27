"""Pictures made by the mflux program, with a stand-in for the program.

No model is loaded and nothing here needs Apple silicon: the adapter is
pointed at a script that keeps what it was given and writes a made-up file.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media.generate import GenerateError, Request
from constructive_media.generate import mflux as painted

from .support import settings_in

WORDS = "a made-up harbour at dawn"

PROGRAM = f"""#!{sys.executable}
import json, sys
from pathlib import Path

given = sys.argv[1:]
Path(__file__).with_name(Path(__file__).name + ".asked").write_text(json.dumps(given))
words = given[given.index("--prompt") + 1]
if "out of paint" in words:
    print("loading the made-up model")
    print("Traceback: something long", file=sys.stderr)
    print("ValueError: the made-up model ran out of paint", file=sys.stderr)
    sys.exit(3)
if "nothing" not in words:
    Path(given[given.index("--output") + 1]).write_bytes(b"made-up picture of " + words.encode())
"""


class MfluxTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        for name in (painted.PROGRAM, painted.EDIT_PROGRAM):
            (self.bin / name).write_text(PROGRAM)
            (self.bin / name).chmod(0o755)
        self.target = self.folder / "work" / "harbour.png"

    def maker(self, models=None, **options):
        return painted.Mflux(settings=settings_in(
            self.folder / "home", models=models or {},
            extras={"image": json.dumps({"program": str(self.bin / painted.PROGRAM), **options})},
        ))

    def asked(self, program=painted.PROGRAM):
        given = json.loads((self.bin / f"{program}.asked").read_text())
        return {given[at]: given[at + 1] for at in range(0, len(given), 2)}

    def test_a_picture_is_made_with_the_defaults(self):
        made = self.maker().make(Request("image", WORDS), self.target)
        self.assertEqual((made, made.read_bytes()), (self.target, b"made-up picture of a made-up harbour at dawn"))
        asked = self.asked()
        out = asked.pop("--output")
        self.assertEqual(asked, {"--model": "flux2-klein-4b", "--prompt": WORDS, "--steps": "4",
                                 "--width": "1344", "--height": "768"})
        # Written somewhere of its own, moved into place, and nothing left behind.
        self.assertNotEqual(Path(out), self.target)
        self.assertEqual(sorted(one.name for one in self.target.parent.iterdir()), ["harbour.png"])
        self.assertTrue((self.folder / "home" / "locks" / "mlx.lock").is_file())

    def test_what_is_asked_for_is_what_the_program_is_given(self):
        self.maker().make(Request("image", WORDS, width=640, height=480, steps=9, seed=7), self.target)
        asked = self.asked()
        self.assertEqual((asked["--width"], asked["--height"], asked["--steps"], asked["--seed"]),
                         ("640", "480", "9", "7"))

    def test_the_model_and_the_options_come_from_settings(self):
        maker = self.maker({"image": "made-up/painter"}, steps=8, quantize=8, guidance=1.5, base_model="flux2-klein-4b")
        self.assertEqual((maker.name, maker.model), ("mflux", "made-up/painter"))
        maker.make(Request("image", WORDS), self.target)
        asked = self.asked()
        self.assertEqual((asked["--model"], asked["--steps"], asked["--quantize"], asked["--guidance"], asked["--base-model"]),
                         ("made-up/painter", "8", "8", "1.5", "flux2-klein-4b"))

    def test_a_picture_to_start_from_goes_to_the_program_that_takes_one(self):
        start = self.folder / "quay.png"
        start.write_bytes(b"made-up")
        self.maker().make(Request("image", WORDS, image=start), self.target)
        self.assertEqual(self.asked(painted.EDIT_PROGRAM)["--image-paths"], str(start))
        self.assertFalse((self.bin / f"{painted.PROGRAM}.asked").exists())
        (self.bin / painted.EDIT_PROGRAM).unlink()
        with self.assertRaisesRegex(GenerateError, f"there is no {painted.EDIT_PROGRAM} at "):
            self.maker().make(Request("image", WORDS, image=start), self.target)

    def test_a_picture_is_written_over_the_one_it_replaces(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"the first")
        self.maker().make(Request("image", WORDS), self.target)
        self.assertEqual(self.target.read_bytes(), b"made-up picture of a made-up harbour at dawn")

    def test_a_program_that_fails_says_why_in_one_line(self):
        with self.assertRaisesRegex(GenerateError, r"^mflux-generate-flux2 could not make the picture: "
                                                   r"ValueError: the made-up model ran out of paint$"):
            self.maker().make(Request("image", "a harbour, out of paint"), self.target)
        with self.assertRaisesRegex(GenerateError, "it gave no reason"):
            self.maker().make(Request("image", "nothing"), self.target)
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_a_program_that_is_not_installed_says_what_to_install(self):
        maker = painted.Mflux(settings=settings_in(self.folder / "home"))
        with mock.patch("shutil.which", return_value=None), \
                self.assertRaisesRegex(GenerateError, r"pip install mflux.*CONSTRUCTIVE_MEDIA_EXTRA_IMAGE"):
            maker.make(Request("image", WORDS), self.target)
        with mock.patch("shutil.which", return_value=str(self.bin / painted.PROGRAM)):
            self.assertEqual(maker.make(Request("image", WORDS), self.target), self.target)
        with self.assertRaisesRegex(GenerateError, "there is no mflux-generate-flux2 at "):
            painted.Mflux(settings=settings_in(self.folder, extras={"image": '{"program": "/made-up/nowhere"}'})).make(
                Request("image", WORDS), self.target)

    def test_what_it_cannot_make_is_refused_before_anything_runs(self):
        with self.assertRaisesRegex(GenerateError, "1010x768 cannot be made.*such as 1008x768"):
            self.maker().make(Request("image", WORDS, width=1010), self.target)
        with self.assertRaisesRegex(GenerateError, "makes pictures, not video"):
            self.maker().make(Request("video", WORDS), self.folder / "work" / "harbour.mp4")
        self.assertFalse((self.bin / f"{painted.PROGRAM}.asked").exists())

    def test_options_it_does_not_know_are_refused(self):
        for extra, said in (("{", "is not JSON"), ("[4]", "should be a JSON object"),
                            ('{"paint": "oil"}', "has paint, which the mflux adapter does not know")):
            with self.subTest(extra), self.assertRaisesRegex(GenerateError, said):
                painted.Mflux(settings=settings_in(self.folder, extras={"image": extra}))


if __name__ == "__main__":
    unittest.main()
