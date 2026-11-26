"""Say whose voice it is.

A voice is found without anyone being known. Naming ties it to a person, and
from then on every line that voice speaks in that recording is theirs. A voice
named in one recording is offered as a suggestion wherever it is heard again,
and only as a suggestion: two recordings are two rooms and two microphones,
and a person decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import SpeakerError
from .voices import named, whose


@dataclass(frozen=True)
class Listed:
    """One voice of one recording, as it stands."""

    label: str
    segments: int
    speech_s: float
    cohesion: float | None
    #: Whose it is.
    persona: str | None
    #: Whose it sounds like, from another recording, when it has no name.
    like: str | None


@dataclass(frozen=True)
class Recording:
    asset_id: Any
    name: str
    model: str | None
    voices: list[Listed]
    #: Segments of the transcript no voice was given.
    unknown: int


def listed(match: str | None = None) -> list[Recording]:
    """Every recording whose voices have been found, and what is known of each."""
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path,
                  (SELECT count(*) FROM media_video.segments s
                    WHERE s.asset_id = a.id AND s.cluster_id IS NULL)
             FROM media.assets a
            WHERE (%s = '' OR a.path ~* %s)
              AND EXISTS (SELECT 1 FROM media_video.speaker_clusters c WHERE c.asset_id = a.id)
            ORDER BY a.ingested_at, a.path""",
        (match or "", match or ""),
    )
    recordings = []
    for asset_id, path, unknown in cur.fetchall():
        cur.execute(
            """SELECT c.label, c.n_segments, c.speech_s, c.cohesion, p.name, c.model, c.centroid
                 FROM media_video.speaker_clusters c
                 LEFT JOIN media.personas p ON p.id = c.persona_id
                WHERE c.asset_id = %s
                ORDER BY c.n_windows DESC NULLS LAST, c.label""",
            (asset_id,),
        )
        rows = cur.fetchall()
        model = next((row[5] for row in rows if row[5]), None)
        others = named(model, asset_id=asset_id, elsewhere=True) if model else []
        voices = []
        for label, segments, speech_s, cohesion, persona, _, centre in rows:
            like = whose(centre, others) if centre and not persona else None
            voices.append(Listed(label, segments or 0, float(speech_s or 0.0),
                                 None if cohesion is None else float(cohesion),
                                 persona, like[0] if like else None))
        recordings.append(Recording(asset_id, Path(path).name, model, voices, unknown))
    return recordings


def name(match: str, label: str, who: str | None, *, new: bool = False, kind: str = "person") -> tuple[str, int]:
    """Say that a recording's voice is `who`, or with None that it is nobody's.

    Returns the recording's name and how many segments the voice speaks.
    `who` is a person's name or slug. With `new`, someone not yet known is added.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT DISTINCT a.id, a.path
             FROM media.assets a
             JOIN media_video.speaker_clusters c ON c.asset_id = a.id
            WHERE a.path ~* %s ORDER BY a.path""",
        (match,),
    )
    found = cur.fetchall()
    if not found:
        raise SpeakerError(f"no recording matching {match!r} has had its voices found; run `me diarize` first")
    if len(found) > 1:
        names = ", ".join(Path(path).name for _, path in found[:5])
        raise SpeakerError(f"{len(found)} recordings match {match!r} ({names}{', ...' if len(found) > 5 else ''}); "
                           "say which one")
    asset_id, path = found[0]
    cur.execute("SELECT label, n_segments FROM media_video.speaker_clusters WHERE asset_id = %s ORDER BY label",
                (asset_id,))
    has = dict(cur.fetchall())
    if label not in has:
        raise SpeakerError(f"{Path(path).name} has no voice {label!r}; it has {', '.join(has)}")
    # Nobody is added for a voice that was not there to be named.
    with db.transaction():
        persona_id = None if who is None else person(who, new=new, kind=kind)
        cur = db.cursor()
        cur.execute("UPDATE media_video.speaker_clusters SET persona_id = %s WHERE asset_id = %s AND label = %s",
                    (persona_id, asset_id, label))
    return Path(path).name, has[label] or 0


def person(who: str, *, new: bool = False, kind: str = "person") -> Any:
    """The id of the person called `who`, by name or slug, adding them if `new`."""
    cur = db.cursor()
    cur.execute(
        "SELECT id FROM media.personas WHERE slug = %s OR lower(name) = lower(%s) ORDER BY created_at",
        (slug(who), who),
    )
    found = cur.fetchall()
    if len(found) > 1:
        raise SpeakerError(f"{len(found)} people are called {who!r}; use the slug of the one you mean")
    if found:
        return found[0][0]
    if not new:
        raise SpeakerError(f"nobody is called {who!r} yet; add --new to add them")
    if not slug(who):
        raise SpeakerError(f"{who!r} has no letters or numbers to make a slug from")
    cur.execute(
        "INSERT INTO media.personas (slug, name, kind) VALUES (%s, %s, %s) RETURNING id",
        (slug(who), who, kind),
    )
    return cur.fetchone()[0]


def slug(text: str) -> str:
    """A name as it is written in commands: lower case, words joined by hyphens."""
    return re.sub(r"[\W_]+", "-", text.lower()).strip("-")
