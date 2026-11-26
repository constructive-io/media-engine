import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import __version__, cli

from .support import HAVE_FFMPEG, HAVE_NUMPY, TEST_DATABASE_VARIABLE, DatabaseTest, make_video


def run(*arguments):
    """Run a command; return its exit status, what it printed, and its errors."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = cli.main(list(arguments))
        except SystemExit as stopped:
            status = stopped.code
    return status, out.getvalue(), err.getvalue()


class CommandLineTest(unittest.TestCase):
    def test_every_command_is_registered_with_help(self):
        status, out, _ = run("--help")
        self.assertEqual(status, 0)
        for command in ("ingest", "lineage", "shots", "frames", "signatures", "detect", "status", "adapters"):
            self.assertIn(command, out)

    def test_version(self):
        self.assertEqual(run("--version")[:2], (0, f"constructive-media {__version__}\n"))

    def test_a_command_is_required_and_must_exist(self):
        self.assertEqual(run()[0], 2)
        self.assertEqual(run("no-such-command")[0], 2)
        self.assertEqual(run("frames", "--per-shot", "9")[0], 2)

    def test_adapters_lists_every_slot_and_marks_the_one_in_use(self):
        status, out, _ = run("adapters")
        self.assertEqual(status, 0)
        for slot in ("database:", "detection:", "effects:", "speech:"):
            self.assertIn(slot, out)
        self.assertRegex(out, r"\* pg8000 +BSD-3-Clause +builtin")
        self.assertRegex(out, r"\* ffmpeg +MIT +builtin")
        self.assertIn("(nothing installed)", out)

    def test_a_database_that_cannot_be_reached_is_one_line_not_a_traceback(self):
        nowhere = {"CONSTRUCTIVE_MEDIA_DATABASE_URL": "postgres://127.0.0.1:1/nowhere",
                   "CONSTRUCTIVE_MEDIA_HOME": "/nowhere"}
        with mock.patch.dict(os.environ, nowhere), mock.patch("constructive_media.db._shared", None):
            status, out, err = run("status")
        self.assertEqual(status, 1)
        self.assertTrue(err.startswith("me: "), err)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertNotIn("Traceback", out + err)

    def test_a_missing_optional_package_is_named(self):
        missing = ModuleNotFoundError("No module named 'numpy'", name="numpy")
        with mock.patch("constructive_media.video.analyze.shots", side_effect=missing):
            status, _, err = run("shots")
        self.assertEqual(status, 1)
        self.assertIn("'numpy'", err)
        self.assertEqual(len(err.splitlines()), 1)

    def test_a_bad_setting_is_reported_the_same_way(self):
        with mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_DATABASE_URL": "mysql://db.example.test/x"}):
            status, _, err = run("status")
        self.assertEqual((status, err.startswith("me: ")), (1, True))


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class CommandsAgainstADatabaseTest(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.video = make_video(cls.root / "footage" / "three.mp4")

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.root / "home"),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_from_a_folder_of_footage_to_shots_frames_and_signatures(self):
        status, out, _ = run("ingest", str(self.root / "footage"), "--project", "demo")
        self.assertEqual(status, 0)
        self.assertIn("ingested", out)
        self.assertIn("1 of 1 files registered", out)

        match = ("--match", "footage/three")
        self.assertIn("3 shots  three.mp4", run("shots", *match)[1])
        self.assertIn("3 frames saved", run("frames", *match, "--per-shot", "1")[1])
        self.assertIn("3 shots signed", run("signatures", *match)[1])
        self.assertIn("0 videos split into shots", run("shots", *match)[1])

        status, out, _ = run("status")
        self.assertEqual(status, 0)
        self.assertIn(self.database.name, out)
        self.assertIn(str(self.root / "home" / "work"), out)
        self.assertRegex(out, r"shots +\d+")
        self.assertNotIn("not deployed", out)

    def test_lineage_of_a_file_nothing_recorded(self):
        status, out, _ = run("lineage", str(self.video))
        self.assertEqual(status, 1)
        self.assertIn("no record of", out)

    def test_a_folder_with_nothing_readable_fails(self):
        empty = self.root / "broken"
        empty.mkdir(exist_ok=True)
        (empty / "broken.mp4").write_bytes(b"not a video")
        status, out, _ = run("ingest", str(empty))
        self.assertEqual(status, 1)
        self.assertIn("0 of 1 files registered", out)


if __name__ == "__main__":
    unittest.main()
