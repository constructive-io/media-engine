"""Keeping house: what the engine has made, and what is lying about that it did not.

Everything the engine writes it writes down, with what it was made from. That
is only worth something if it can be read back the other way too: not "what
is this file?", which `lineage` answers, but "what files are there?". A work
folder fills up over a project, and the day it has to be cleared out the
question is which of it can be made again and which cannot.

Two lists answer it:

- `made` is every derived file on record: what kind it is, what it was made
  from, and whether it is still where it was written. A record whose file is
  gone is not an error. It is how to make the file again.
- `unrecorded` is the other side: the pictures and sound in a folder that
  nothing on record names. Nobody can say what those were made from, so they
  are what to look at before deleting anything.

A file is on record when any table names it: a recording that was ingested,
the small copy made of it, a render, a line that was spoken, the clip a voice
was made from, the card cut for a voice found in a recording. Only pictures
and sound are looked for. The frames the analysis samples are stills in their
thousands, and are made again from their recordings for the asking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, derive
from .ingest import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from .settings import Settings


@dataclass(frozen=True)
class Output:
    """One derived file on record."""

    path: Path
    kind: str
    tool: str
    duration_s: float | None
    #: The names of the files it was made from.
    sources: tuple[str, ...]
    params: Mapping[str, Any]
    created_at: datetime
    #: Whether the file is still where it was written.
    there: bool


def made(kind: str | None = None) -> list[Output]:
    """Every derived file on record, or every one of a kind, in the order they were made."""
    if kind is not None and kind not in derive.KINDS:
        raise ValueError(f"unknown kind {kind!r}; kinds are: {', '.join(derive.KINDS)}")
    cur = db.cursor()
    cur.execute(
        """SELECT output_path, kind, tool, output_duration_s, sources, params, created_at
             FROM media.v_derivations
            WHERE (%s::text IS NULL OR kind = %s)
            ORDER BY created_at, output_path""",
        (kind, kind),
    )
    return [Output(Path(path), kind, tool, None if duration is None else float(duration),
                   tuple(name for name in sources or () if name), params or {}, created_at, Path(path).is_file())
            for path, kind, tool, duration, sources, params, created_at in cur.fetchall()]


def recorded(settings: Settings | None = None) -> set[Path]:
    """Every file some table names, wherever it is."""
    from .speakers import cards

    settings = settings or Settings.from_env()
    cur = db.cursor()
    cur.execute(
        """SELECT path FROM media.assets
           UNION SELECT proxy_path FROM media.assets WHERE proxy_path IS NOT NULL
           UNION SELECT reference_audio_path FROM media.voices WHERE reference_audio_path IS NOT NULL
           UNION SELECT output_path FROM media_audio.renders WHERE output_path IS NOT NULL
           UNION SELECT audio_path FROM media_audio.render_chunks WHERE audio_path IS NOT NULL
           UNION SELECT audio_path FROM media_audio.review_queue WHERE audio_path IS NOT NULL"""
    )
    known = {_whole(path) for (path,) in cur.fetchall()}
    # A voice's card is named by where it is kept, not by a column.
    cur.execute("SELECT asset_id, label FROM media_video.speaker_clusters")
    known |= {_whole(cards.folder(asset_id, settings) / f"{label}.wav") for asset_id, label in cur.fetchall()}
    return known


def unrecorded(folder: str | Path, settings: Settings | None = None) -> list[Path]:
    """The pictures and sound under `folder` that nothing on record names, in order of path."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")
    known = recorded(settings)
    suffixes = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
    found = []
    for file in sorted(root.rglob("*")):
        if file.suffix.lower() not in suffixes or not file.is_file():
            continue
        if any(part.startswith(".") for part in file.relative_to(root).parts):
            continue
        if _whole(file) not in known:
            found.append(file)
    return found


def _whole(path: str | Path) -> Path:
    """A path as it is compared: from the root, with nothing in it that points elsewhere."""
    return Path(path).expanduser().resolve()
