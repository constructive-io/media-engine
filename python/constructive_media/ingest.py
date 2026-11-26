"""Ingest: probe, proxy, register. The step that makes everything else cheap.

After ingest nothing reads the original again. Camera originals can be very
large frames in a slow codec, and decoding one repeatedly to sample frames is
the easiest way to turn an overnight run into a three-day one. Analysis reads
a small proxy instead.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, ffmpeg
from .settings import Settings

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".webm"})
AUDIO_EXTENSIONS = frozenset({".wav", ".m4a", ".mp3", ".aiff", ".flac", ".ogg"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".webp"})

#: Folders that hold what was made from footage, by convention.
OUTPUT_FOLDERS = frozenset({"edits", "exports", "previews", "proxies", "renders", "work"})

PROXY_HEIGHT = 720
HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class Ingested:
    """What happened to one file."""

    name: str
    #: `ingested`, `have`, `unreadable`, or `failed: <reason>`.
    state: str
    asset_id: Any = None


def quick_hash(path: str | Path, chunk: int = HASH_CHUNK) -> str:
    """Size, head and tail: not the whole file.

    A shoot is tens of gigabytes and hashing it end to end costs minutes per
    file. This answers "have I already ingested this", not "was it forged".
    """
    digest = hashlib.sha256()
    size = Path(path).stat().st_size
    digest.update(str(size).encode())
    with open(path, "rb") as handle:
        digest.update(handle.read(chunk))
        if size > chunk * 2:
            handle.seek(-chunk, os.SEEK_END)
            digest.update(handle.read(chunk))
    return digest.hexdigest()


def make_proxy(source: str | Path, target: str | Path) -> Path | None:
    """Write a small H.264 copy of a video for analysis, or None if it failed."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg.encode("-i", source, "-vf", f"scale=-2:{PROXY_HEIGHT}",
                      "-c:v", "{vcodec}", "-b:v", "4M", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "128k", target)
    except ffmpeg.FfmpegError:
        target.unlink(missing_ok=True)
        return None
    return target if target.exists() else None


def candidates(
    path: str | Path, *, skip_folders: frozenset[str] = OUTPUT_FOLDERS,
    work_dir: Path | None = None,
) -> list[Path]:
    """The media files at `path`: the file itself, or everything in the folder.

    The engine's own output is left out. An edit written inside the folder it
    was cut from would otherwise be ingested as source the next time round,
    transcribed, and offered as material to cut from, and the library would
    slowly fill with copies of itself.
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        return [root]
    extensions = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS
    work = work_dir.resolve() if work_dir else None
    found = []
    for file in sorted(root.rglob("*")):
        if file.suffix.lower() not in extensions or not file.is_file():
            continue
        if skip_folders & {part.lower() for part in file.relative_to(root).parts[:-1]}:
            continue
        if work and work in file.resolve().parents:
            continue
        found.append(file)
    return found


def ingest(
    path: str | Path, project: str | None = None, *, settings: Settings | None = None,
) -> list[Ingested]:
    """Register a file, or every media file in a folder, as assets."""
    settings = settings or Settings.from_env()
    cur = db.cursor()
    results = []
    for file in candidates(path, work_dir=settings.work_dir):
        # One bad file costs one file. A card is exactly where something
        # unreadable turns up, and the worst place to be all-or-nothing.
        try:
            results.append(_ingest_one(cur, file, project, settings))
        except Exception as error:  # noqa: BLE001
            results.append(Ingested(file.name, f"failed: {type(error).__name__}: {error}"))
    return results


def _ingest_one(cur: Any, file: Path, project: str | None, settings: Settings) -> Ingested:
    if file.suffix.lower() in IMAGE_EXTENSIONS:
        described = ffmpeg.Probe("image")
    else:
        described = ffmpeg.probe(file)
    if described is None:
        return Ingested(file.name, "unreadable")
    content_hash = quick_hash(file)
    # Known by its path, as a file the engine made and recorded is, or else
    # by its bytes, wherever they now are.
    location = str(file.resolve())
    cur.execute(
        """SELECT id FROM media.assets WHERE content_hash = %s OR path = %s
            ORDER BY path = %s DESC LIMIT 1""",
        (content_hash, location, location),
    )
    if row := cur.fetchone():
        if project:
            cur.execute("UPDATE media.assets SET project = %s WHERE id = %s AND project IS NULL",
                        (project, row[0]))
        return Ingested(file.name, "have", row[0])
    proxy = None
    if described.medium == "video":
        proxy = make_proxy(file, settings.work_dir / "proxies" / f"{content_hash[:12]}.mp4")
    cur.execute(
        """INSERT INTO media.assets
             (medium, path, content_hash, duration_s, width, height,
              fps, codec, has_audio, proxy_path, project)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (content_hash) DO UPDATE
             SET project = coalesce(EXCLUDED.project, media.assets.project)
           RETURNING id""",
        (described.medium, location, content_hash, described.duration_s,
         described.width, described.height, described.fps, described.codec,
         described.has_audio, str(proxy) if proxy else None, project),
    )
    return Ingested(file.name, "ingested", cur.fetchone()[0])
