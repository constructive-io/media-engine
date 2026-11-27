"""Takes made and mended by hand.

`group`, `match` and `sync` work from what the files say about themselves:
their clocks, their words, their sound. Sometimes none of that is there to go
on. A recorder's clock was never set, a camera heard only music, two devices
share so little that nothing can place one against the other. A person who was
in the room still knows which files belong together, and after listening knows
how far apart they are.

So everything the other pieces decide can also be said outright: which
recordings are one take, which of them is the primary, what each one is to the
take, and how far a recording is from the primary. An offset said here is
stored as `manual`, and `sync` never moves it: it builds on it, as it builds
on the primary.

Two things are refused, because doing them quietly would break what is
already there:

- A take's passages are kept in take time, and take time is the primary's
  own. Once a take has passages its primary is not changed.
- A recording nothing has placed cannot become the primary of a take whose
  other recordings are placed, since there would be nothing to carry them
  across with. Where it is placed, every offset is carried over, so no
  recording moves against any other.

A recording is named the way a person would: by its label in the take, its
path, its file name, or a pattern that fits one path and no other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import ROLES, Member, Take, TakeError, create, members

#: Recordings that are something the engine made from other recordings. A
#: snippet cut from a take has the take's name and a label in its path, and
#: must not be what a pattern finds.
_NOT_DERIVED = "NOT EXISTS (SELECT 1 FROM media.derivations d WHERE d.output_id = a.id)"


@dataclass(frozen=True)
class Recording:
    """A recording in the library."""

    asset_id: Any
    path: str
    medium: str

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass(frozen=True)
class Wanted:
    """A recording as it is asked for on the command line: `RECORDING[:ROLE[:LABEL]]`."""

    named: str
    role: str | None = None
    label: str | None = None


def wanted(spec: str) -> Wanted:
    """Read `RECORDING`, `RECORDING:ROLE` or `RECORDING:ROLE:LABEL`."""
    named, _, rest = spec.partition(":")
    role, _, label = rest.partition(":")
    if not named.strip():
        raise TakeError(f"{spec!r} names no recording; write RECORDING[:ROLE[:LABEL]]")
    if role and role not in ROLES:
        raise TakeError(f"{role!r} is not something a recording can be to a take; it can be: {', '.join(ROLES)}")
    return Wanted(named.strip(), role or None, label.strip() or None)


def _pattern(named: str) -> str:
    try:
        re.compile(named)
    except re.error as error:
        raise TakeError(f"{named!r} is neither a recording's name nor a pattern: {error}") from None
    return named


def _one(found: list[Any], named: str, where: str, names: list[str]) -> Any:
    if not found:
        raise TakeError(f"no recording {where} is called or matches {named!r}")
    if len(found) > 1:
        shown = ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
        raise TakeError(f"{len(found)} recordings {where} match {named!r} ({shown}); say which one")
    return found[0]


def recording(named: str) -> Recording:
    """The one recording in the library that `named` picks out."""
    cur = db.cursor()
    cur.execute(
        f"""SELECT a.id, a.path, a.medium FROM media.assets a
             WHERE a.medium IN ('video', 'audio') AND {_NOT_DERIVED}
               AND (a.path = %s OR substring(a.path from '[^/]+$') = %s)
             ORDER BY a.path""",
        (named, named),
    )
    found = cur.fetchall()
    if not found:
        cur.execute(
            f"""SELECT a.id, a.path, a.medium FROM media.assets a
                 WHERE a.medium IN ('video', 'audio') AND {_NOT_DERIVED} AND a.path ~* %s
                 ORDER BY a.path""",
            (_pattern(named),),
        )
        found = cur.fetchall()
    return Recording(*_one(found, named, "in the library", [Path(path).name for _, path, _ in found]))


def fits(take: Take, named: str) -> list[Member]:
    """The recordings in `take` that `named` could mean: an exact name if there is one, else what a pattern fits."""
    inside = members(take.id)
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path FROM media.take_assets ta JOIN media.assets a ON a.id = ta.asset_id
            WHERE ta.take_id = %s""",
        (take.id,),
    )
    paths = dict(cur.fetchall())
    exact = [m for m in inside
             if named in (m.label, paths[m.asset_id], Path(paths[m.asset_id]).name, Path(paths[m.asset_id]).stem)]
    if exact:
        return exact
    cur.execute(
        """SELECT a.id FROM media.take_assets ta JOIN media.assets a ON a.id = ta.asset_id
            WHERE ta.take_id = %s AND (a.path ~* %s OR ta.label ~* %s)""",
        (take.id, _pattern(named), named),
    )
    found = {asset_id for (asset_id,) in cur.fetchall()}
    return [m for m in inside if m.asset_id in found]


def member(take: Take, named: str | Member) -> Member:
    """The one recording in `take` that `named` picks out."""
    if isinstance(named, Member):
        return named
    found = fits(take, named)
    return _one(found, named, f"in take {take.name!r}", [m.label for m in found])


def open_take(name: str, project: str | None = None) -> tuple[Take, bool]:
    """The take called `name`, made if there is none. Returns it, and whether it was made.

    A project given for a take that exists is the take's project from then on.
    """
    cur = db.cursor()
    cur.execute("SELECT id, name, project FROM media.takes WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        return Take(create(name, project), name, project), True
    if project is not None and project != row[2]:
        cur.execute("UPDATE media.takes SET project = %s WHERE id = %s", (project, row[0]))
        return Take(row[0], name, project), False
    return Take(*row), False


def put(take: Take, want: Wanted, *, primary: bool = False) -> tuple[Member, str]:
    """Put a recording in a take, or change what it is to a take it is already in.

    Returns the member and what happened: `added`, `changed` or `as it was`.
    A recording new to the take is not placed: `sync` has that to do, or
    `place`. `primary` is for the first recording of a take with no primary.
    """
    already = fits(take, want.named)
    if len(already) > 1:
        _one(already, want.named, f"in take {take.name!r}", [m.label for m in already])
    inside = members(take.id)
    cur = db.cursor()
    if already:
        now = already[0]
        asset_id = now.asset_id
        role, label = want.role or now.role, want.label or now.label
        if label != now.label and label in {m.label for m in inside}:
            raise TakeError(f"another recording in take {take.name!r} is already labelled {label!r}")
        if (role, label) == (now.role, now.label):
            return now, "as it was"
        # A label nobody gave stays ungiven: it is shown as the recording's path, and is not that text.
        cur.execute(
            """UPDATE media.take_assets SET role = %s, label = coalesce(%s::text, label)
                WHERE take_id = %s AND asset_id = %s""",
            (role, want.label, take.id, asset_id))
        happened = "changed"
    else:
        found = recording(want.named)
        asset_id = found.asset_id
        used = {m.label for m in inside}
        if want.label in used:
            raise TakeError(f"another recording in take {take.name!r} is already labelled {want.label!r}")
        label = want.label or Path(found.path).stem
        if label in used:
            label = f"{label}-{str(asset_id)[:6]}"
        cur.execute(
            """INSERT INTO media.take_assets (take_id, asset_id, role, label, is_primary)
               VALUES (%s, %s, %s, %s, %s)""",
            (take.id, asset_id, want.role or ("camera" if found.medium == "video" else "lav"), label, primary),
        )
        happened = "added"
    return next(m for m in members(take.id) if m.asset_id == asset_id), happened


def elsewhere(take: Take, asset_id: Any) -> list[str]:
    """The other takes a recording is in."""
    cur = db.cursor()
    cur.execute(
        """SELECT t.name FROM media.take_assets ta JOIN media.takes t ON t.id = ta.take_id
            WHERE ta.asset_id = %s AND ta.take_id <> %s ORDER BY t.name""",
        (asset_id, take.id),
    )
    return [name for (name,) in cur.fetchall()]


def place(take: Take, named: str | Member, offset_s: float) -> Member:
    """Say how far a recording is from the primary: the seconds to add to its time to reach take time."""
    found = member(take, named)
    if found.is_primary:
        raise TakeError(f"{found.label} is the primary of take {take.name!r}: take time is its own time, "
                        "and its offset is nothing by definition")
    db.cursor().execute(
        """UPDATE media.take_assets SET offset_s = %s::float8, offset_method = 'manual', offset_confidence = NULL
            WHERE take_id = %s AND asset_id = %s""",
        (float(offset_s), take.id, found.asset_id),
    )
    return found


def forget(take: Take, named: str | Member) -> Member:
    """Let go of a recording's offset, however it was arrived at, so `sync` places it afresh."""
    found = member(take, named)
    if found.is_primary:
        raise TakeError(f"{found.label} is the primary of take {take.name!r} and has no offset to forget")
    db.cursor().execute(
        """UPDATE media.take_assets SET offset_s = 0, offset_method = NULL, offset_confidence = NULL
            WHERE take_id = %s AND asset_id = %s""",
        (take.id, found.asset_id),
    )
    return found


def remove(take: Take, named: str | Member) -> Member:
    """Take a recording out of a take. The recording itself is untouched."""
    found = member(take, named)
    if found.is_primary:
        raise TakeError(f"{found.label} is the primary of take {take.name!r}; make another recording "
                        "the primary before taking it out")
    db.cursor().execute("DELETE FROM media.take_assets WHERE take_id = %s AND asset_id = %s",
                        (take.id, found.asset_id))
    return found


def lead(take: Take, named: str | Member) -> tuple[Member, bool]:
    """Make a recording of a take its primary. Returns it, and whether anything changed.

    Take time becomes the new primary's own time, and every offset is carried
    over to it: what was `offset` becomes `offset - the new primary's offset`,
    the old primary included, which is then as well placed as the new one was.
    """
    found = member(take, named)
    if found.is_primary:
        return found, False
    inside = members(take.id)
    before = next((m for m in inside if m.is_primary), None)
    # A take that has lost its primary still has offsets that agree with each other, and they are carried the same way.
    shift = found.offset_s if found.placed else 0.0
    cur = db.cursor()
    cur.execute("SELECT count(*) FROM media_video.passages WHERE take_id = %s", (take.id,))
    (kept,) = cur.fetchone()
    if kept and (before is not None or shift):
        whose = f"its primary, {before.label}" if before is not None else "the primary it had"
        raise TakeError(f"take {take.name!r} has {kept} passages kept in the time of {whose}; "
                        "its primary is not changed. Make another take instead")
    if not found.placed and any(m.placed for m in inside if not m.is_primary):
        raise TakeError(f"{found.label} has not been placed in take {take.name!r}, so the other recordings "
                        "cannot be carried over to its time; place it with `media sync` or `--offset` first")
    cur.execute(
        """UPDATE media.take_assets SET offset_s = offset_s - %s::float8
            WHERE take_id = %s AND offset_method IS NOT NULL""",
        (shift, take.id),
    )
    if before is not None:
        # The old primary is now as far from the new one as the new one was from it, and as well measured.
        cur.execute(
            """UPDATE media.take_assets
                  SET is_primary = false, offset_s = %s::float8, offset_method = %s, offset_confidence = %s
                WHERE take_id = %s AND asset_id = %s""",
            (0.0 - shift, found.method, found.confidence, take.id, before.asset_id),
        )
    cur.execute(
        """UPDATE media.take_assets
              SET is_primary = true, offset_s = 0, offset_method = NULL, offset_confidence = NULL
            WHERE take_id = %s AND asset_id = %s""",
        (take.id, found.asset_id),
    )
    return found, True
