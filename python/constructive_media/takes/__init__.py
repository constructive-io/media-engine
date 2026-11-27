"""Takes: one moment, recorded by several devices.

A camera and three microphones in one room make four files that know nothing
of each other. A take says they are one event, and gives each a place on one
shared timeline, so the good sound can go with the good picture.

Devices do not share a clock and do not start together, so each recording in
a take carries an offset:

    take time = the recording's own time + its offset

One recording, the primary, defines take time and has offset zero. Everything
in this package measures in that direction and no other. A recording whose
offset nothing has measured has no method beside it, and is not to be trusted
on the timeline until `sync` has placed it.

The pieces, in the order they are used:

- `clock`   when each file says it was recorded
- `group`   files recording at the same time become a take
- `match`   a recording with no usable clock finds its camera by what is said
- `sync`    each recording is placed on the take's timeline, and the placing
            is checked by content
- `align`, `check`, `loudness`   the measurements those rest on
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import db

#: What a recording is to its take.
ROLES = ("camera", "lav", "ambient", "board", "screen")
#: How an offset was arrived at.
METHODS = ("words", "phrases", "loudness", "clock", "manual")


class TakeError(LookupError):
    """A take that was asked for does not exist, or cannot be made."""


@dataclass(frozen=True)
class Member:
    """One recording in a take."""

    asset_id: Any
    label: str
    role: str
    medium: str
    #: The file to read: the proxy where there is one, else the original.
    source: str
    duration_s: float | None
    is_primary: bool
    #: Seconds to add to this recording's own time to reach take time.
    offset_s: float
    confidence: float | None
    #: One of `METHODS`, or None when nothing has placed it.
    method: str | None
    recorded_at: datetime | None
    has_audio: bool

    @property
    def placed(self) -> bool:
        return self.is_primary or self.method is not None


@dataclass(frozen=True)
class Take:
    id: Any
    name: str
    project: str | None
    members: int = 0
    placed: int = 0


def find(name: str) -> Take:
    """The take called `name`."""
    cur = db.cursor()
    cur.execute("SELECT id, name, project FROM media.takes WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        raise TakeError(f"no take is called {name!r}; `media takes` lists them")
    return Take(*row)


def listing(project: str | None = None) -> list[Take]:
    """Every take, or one project's, with how much of each is placed."""
    cur = db.cursor()
    cur.execute(
        """SELECT t.id, t.name, t.project, count(ta.asset_id),
                  count(ta.asset_id) FILTER (WHERE ta.is_primary OR ta.offset_method IS NOT NULL)
             FROM media.takes t
             LEFT JOIN media.take_assets ta ON ta.take_id = t.id
            WHERE %s::text IS NULL OR t.project = %s
            GROUP BY t.id ORDER BY t.name""",
        (project, project),
    )
    return [Take(*row) for row in cur.fetchall()]


def members(take_id: Any) -> list[Member]:
    """The recordings in a take, the primary first."""
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, coalesce(ta.label, a.path), ta.role, a.medium,
                  coalesce(a.proxy_path, a.path), a.duration_s, ta.is_primary, ta.offset_s,
                  ta.offset_confidence, ta.offset_method, a.recorded_at, a.has_audio
             FROM media.take_assets ta
             JOIN media.assets a ON a.id = ta.asset_id
            WHERE ta.take_id = %s
            ORDER BY ta.is_primary DESC, ta.role, ta.label, a.path""",
        (take_id,),
    )
    return [Member(*row) for row in cur.fetchall()]


def create(name: str, project: str | None) -> Any:
    """Make an empty take and return its id."""
    cur = db.cursor()
    cur.execute("SELECT 1 FROM media.takes WHERE name = %s", (name,))
    if cur.fetchone():
        raise TakeError(f"a take called {name!r} already exists")
    cur.execute("INSERT INTO media.takes (name, project) VALUES (%s, %s) RETURNING id", (name, project))
    return cur.fetchone()[0]


def add(
    take_id: Any, asset_id: Any, *, role: str, label: str, is_primary: bool = False,
    offset_s: float = 0.0, confidence: float | None = None, method: str | None = None,
) -> None:
    """Put a recording in a take."""
    db.cursor().execute(
        """INSERT INTO media.take_assets
                  (take_id, asset_id, role, label, is_primary, offset_s, offset_confidence, offset_method)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (take_id, asset_id, role, label, is_primary, offset_s, confidence, method),
    )


def next_name(prefix: str, taken: set[str] | None = None) -> str:
    """The first of `prefix-01`, `prefix-02`, ... that no take has."""
    cur = db.cursor()
    cur.execute("SELECT name FROM media.takes WHERE name LIKE %s", (prefix.replace("%", "") + "-%",))
    used = {name for (name,) in cur.fetchall()} | (taken or set())
    number = 1
    while f"{prefix}-{number:02d}" in used:
        number += 1
    return f"{prefix}-{number:02d}"
