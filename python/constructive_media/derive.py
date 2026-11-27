"""Record an output file as an asset, with an edge back to its sources.

One function, called by everything that writes a file. Writing a file is one
line and recording it is five, so when each producer keeps its own records the
recording is what gets skipped.

A derived file is identified by its path, not its bytes. Rendering the same
timeline twice gives different bytes, because an encode is not reproducible
bit for bit, so keying on content would leave two assets and two edges for one
file on disk. Ingested sources are the other way round: the same footage
copied to two folders is one asset.

So the hash stored for a derived file covers its path as well as its bytes. An
output that matches a source byte for byte, as a straight copy does, is still
its own asset and never takes the source's place.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, ffmpeg
from .ingest import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, quick_hash

KINDS = ("proxy", "render", "snippet", "stem", "still", "export", "generated")


@dataclass(frozen=True)
class Source:
    """One input to a derived file.

    The times are in the source's own clock, the same convention the analysis
    tables use, so provenance joins against them without conversion. Leave
    them unset when the whole file was used.
    """

    asset_id: Any
    role: str = "source"
    start_s: float | None = None
    end_s: float | None = None


@dataclass(frozen=True)
class Lineage:
    """What a file was made from, and how to make it again."""

    kind: str
    tool: str
    params: Mapping[str, Any]
    start_s: float | None
    end_s: float | None
    sources: tuple[str, ...]
    created_at: datetime


def record(
    path: str | Path, kind: str, tool: str, sources: Iterable[Source] = (), *,
    params: Mapping[str, Any] | None = None, start_s: float | None = None,
    end_s: float | None = None, project: str | None = None,
) -> Any:
    """Register `path` as a derived asset and return its asset id.

    Calling it again for the same path replaces the record: the file there, as
    it exists now, came from exactly one act of making it.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; kinds are: {', '.join(KINDS)}")
    file = Path(path).resolve()
    described = ffmpeg.probe(file) if file.suffix.lower() not in IMAGE_EXTENSIONS else None
    medium = (described.medium if described
              else "image" if file.suffix.lower() in IMAGE_EXTENSIONS
              else "audio" if file.suffix.lower() in AUDIO_EXTENSIONS else "video")
    described = described or ffmpeg.Probe(medium)
    facts = (_identity(file), described.duration_s, described.width, described.height,
             described.fps, described.codec, described.has_audio)
    cur = db.cursor()
    cur.execute("SELECT id FROM media.assets WHERE path = %s", (str(file),))
    if row := cur.fetchone():
        asset_id = row[0]
        cur.execute(
            """UPDATE media.assets
                  SET content_hash = %s, duration_s = %s, width = %s, height = %s,
                      fps = %s, codec = %s, has_audio = %s
                WHERE id = %s""",
            (*facts, asset_id),
        )
    else:
        cur.execute(
            """INSERT INTO media.assets
                 (medium, path, content_hash, duration_s, width, height,
                  fps, codec, has_audio, project)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (medium, str(file), *facts, project),
        )
        asset_id = cur.fetchone()[0]
    cur.execute("DELETE FROM media.derivations WHERE output_id = %s", (asset_id,))
    cur.execute(
        """INSERT INTO media.derivations (output_id, kind, tool, params, start_s, end_s)
           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
        (asset_id, kind, tool, db.jsonb(dict(params or {})), start_s, end_s),
    )
    derivation_id = cur.fetchone()[0]
    for source in sources:
        if source.asset_id is None:
            continue
        cur.execute(
            """INSERT INTO media.derivation_sources
                 (derivation_id, source_id, role, start_s, end_s)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (derivation_id, source.asset_id, source.role or "source",
             source.start_s, source.end_s),
        )
    return asset_id


def _identity(file: Path) -> str:
    """The hash of a derived file: where it is, then what is in it."""
    return hashlib.sha256(f"{file}\0{quick_hash(file)}".encode()).hexdigest()


def lineage(path: str | Path) -> Lineage | None:
    """What the file at `path` was made from, or None if nothing recorded it."""
    cur = db.cursor()
    cur.execute(
        """SELECT kind, tool, params, start_s, end_s, sources, created_at
             FROM media.v_derivations
            WHERE output_path = %s""",
        (str(Path(path).resolve()),),
    )
    row = cur.fetchone()
    if row is None:
        return None
    kind, tool, params, start_s, end_s, sources, created_at = row
    return Lineage(kind, tool, params or {}, start_s, end_s,
                   tuple(name for name in sources or () if name), created_at)
