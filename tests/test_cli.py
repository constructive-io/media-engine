import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import __version__, cli
from constructive_media.transcription import Segment, Word

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
        for command in ("ingest", "lineage", "shots", "frames", "signatures", "detect",
                        "transcribe", "vocabulary", "group", "match", "sync", "takes", "status",
                        "adapters"):
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
        self.assertRegex(out, r"\* whispercpp +MIT +builtin")
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

    def test_a_recogniser_that_cannot_run_is_reported_the_same_way(self):
        from constructive_media.models import ModelError
        from constructive_media.transcription import TranscriptionError

        for failure in (TranscriptionError("the 'whisper-cli' program was not found"),
                        ModelError("no transcription model called 'enormous'")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("constructive_media.audio.transcripts.transcribe", side_effect=failure), \
                    mock.patch("constructive_media.transcription.transcriber"):
                status, _, err = run("transcribe")
            self.assertEqual((status, err), (1, f"me: {failure}\n"))


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


class Listener:
    """A recogniser that hears one sentence in everything."""

    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.asked = []

    def transcribe(self, audio, *, language="en", prompt=None):
        self.asked.append((audio.name, language, prompt))
        words = tuple(Word(n * 0.5, n * 0.5 + 0.4, word) for n, word in enumerate("We sailed at dawn.".split()))
        return [Segment(0.0, 2.0, "We sailed at dawn.", words)]


class ListeningCommandsTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": "/made-up/home",
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_terms_are_added_listed_and_listened_for(self):
        self.assertEqual(run("vocabulary", "Zentrova", "Ottoline Varga", "--project", "made-up-harbour",
                             "--kind", "company")[:2], (0, "2 of 2 terms added\n"))
        self.assertEqual(run("vocabulary", "Zentrova", "--project", "made-up-harbour")[1], "0 of 1 terms added\n")
        status, out, _ = run("vocabulary", "--project", "made-up-harbour")
        self.assertEqual(status, 0)
        self.assertIn("  Ottoline Varga\n  Zentrova\n", out)
        self.assertRegex(out, r"\n\d+ terms\n$")

        self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project)
               VALUES ('audio', '/made-up/harbour.wav', 'made-up-harbour', true, 'made-up-harbour') RETURNING id""")
        listener = Listener()
        with mock.patch("constructive_media.transcription.transcriber", return_value=listener) as chosen:
            status, out, _ = run("transcribe", "--match", "^/made-up/", "--language", "fr", "--adapter", "fake")
            again = run("transcribe", "--match", "^/made-up/", "--no-vocabulary")[1]
            forced = run("transcribe", "--match", "^/made-up/", "--no-vocabulary", "--force")[1]
        self.assertEqual(status, 0)
        self.assertEqual(out, "      1 segments  harbour.wav\n1 recordings transcribed with fake (fake-1)\n")
        self.assertEqual(again, "0 recordings transcribed with fake (fake-1)\n")
        self.assertIn("1 recordings transcribed", forced)
        chosen.assert_any_call("fake")
        (name, language, prompt), (_, _, unprompted) = listener.asked
        self.assertEqual((name, language, unprompted), ("harbour.wav", "fr", None))
        self.assertIn("Ottoline Varga, Zentrova", prompt)

        status, out, _ = run("status")
        self.assertRegex(out, r"segments +\d+")
        self.assertRegex(out, r"words +\d+")


if __name__ == "__main__":
    unittest.main()
