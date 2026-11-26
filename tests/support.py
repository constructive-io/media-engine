"""What the tests share: a database to roll back, and media made on the spot.

The repository holds no media. Every picture and sound a test needs is made by
ffmpeg from its built-in test sources, in a temporary folder.

Tests that touch the database run only when `CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL`
names a database with the schema modules deployed. Each runs in a transaction
that is rolled back, so they leave nothing behind.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import unittest
from pathlib import Path

from constructive_media import db, ffmpeg
from constructive_media.settings import Database, Settings

TEST_DATABASE_VARIABLE = "CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None

needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def settings_in(folder: str | Path, **changes) -> Settings:
    """Settings that keep everything the engine writes inside `folder`."""
    home = Path(folder)
    return Settings(home=home, work_dir=home / "work", models_dir=home / "models", **changes)


class DatabaseTest(unittest.TestCase):
    """A test whose every statement is undone when it ends."""

    def setUp(self):
        url = os.environ.get(TEST_DATABASE_VARIABLE)
        if not url:
            self.skipTest(f"{TEST_DATABASE_VARIABLE} is not set")
        if importlib.util.find_spec("pg8000") is None:
            self.skipTest("pg8000 is not installed")
        self.database = Database.from_url(url)
        self.connection = db.driver().connect(self.database, autocommit=False)
        scope = db.use(self.connection)
        scope.__enter__()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.addCleanup(scope.__exit__, None, None, None)

    def query(self, sql, params=()):
        return db.cursor().execute(sql, params).fetchall()


def make_video(path: Path, colours=("red", "0x103818", "0xd0d8ff"), seconds: float = 1.0,
               size: str = "160x90", rate: int = 25) -> Path:
    """A video of flat colours, one per shot, with a tone under it.

    The default is bright red, then dark green, then pale blue.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for colour in colours:
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:r={rate}:d={seconds}"]
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds * len(colours)}"]
    joined = "".join(f"[{n}:v]" for n in range(len(colours)))
    ffmpeg.encode(
        *inputs, "-filter_complex", f"{joined}concat=n={len(colours)}:v=1[v]",
        "-map", "[v]", "-map", f"{len(colours)}:a", "-c:v", "{vcodec}", "-b:v", "1M",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
    )
    return path


def make_tone(path: Path, seconds: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"sine=frequency=440:duration={seconds}", path)
    return path


def make_still(path: Path, size: str = "160x90") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"testsrc=s={size}", "-frames:v", "1", "-update", "1", path)
    return path
