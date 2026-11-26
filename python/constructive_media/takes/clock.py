"""When each file says it was recorded.

Every device writes the time somewhere: recorders in the file name, cameras
inside the file. That time says which files were rolling at the same moment,
which is what makes them a take.

It is a hint and not a measurement. Device clocks are set by hand, they
drift, and some cameras write the moment a file closed. So the clock decides
which recordings *could* belong together, and what is said in them decides
exactly where each sits. Only when several devices' clocks have been shown to
agree, by recordings already placed, is a clock trusted to place one more.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .. import db, ffmpeg

#: A date and a time of day inside a file name: `20260102_030405`,
#: `2026-01-02 03.04.05`, and what lies between.
_IN_NAME = (
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[ _T-]?(\d{2})(\d{2})(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.:-](\d{2})[.:-](\d{2})(?!\d)"),
)
#: A recorder whose clock was never set says it is its year of manufacture or
#: 1970. Such a time is worse than none.
_EARLIEST_YEAR = 2005


def zone(utc_offset_hours: float | None) -> timezone | None:
    """The time zone a number of hours from UTC names; None for this computer's."""
    return None if utc_offset_hours is None else timezone(timedelta(hours=utc_offset_hours))


def from_name(name: str, tz: timezone | None = None) -> datetime | None:
    """The time written in a file name, if one is.

    A file name carries no time zone. It is read in `tz`, or in this
    computer's zone, on the guess that the recorder was set where it is used.
    """
    for pattern in _IN_NAME:
        for found in pattern.finditer(name):
            try:
                written = datetime(*map(int, found.groups()))
            except ValueError:
                continue
            if _plausible(written.year):
                return written.replace(tzinfo=tz) if tz else written.astimezone()
    return None


def read(path: str | Path, tz: timezone | None = None) -> tuple[datetime, str] | None:
    """When a file was recorded and where that was read: `filename` or `container`."""
    if found := from_name(Path(path).name, tz):
        return found, "filename"
    if (inside := ffmpeg.created(path)) and _plausible(inside.year):
        return inside, "container"
    return None


def stamp(project: str | None = None, *, utc_offset_hours: float | None = None) -> dict[str, int]:
    """Record a time for each recording that has none yet.

    A file is looked at once. What was found, or that nothing was, is kept,
    so the next run does not open it again.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT id, path FROM media.assets
            WHERE recorded_at IS NULL AND meta->>'time_from' IS NULL
              AND medium IN ('video', 'audio')
              AND (%s::text IS NULL OR project = %s)
            ORDER BY path""",
        (project, project),
    )
    tz = zone(utc_offset_hours)
    counts = {"filename": 0, "container": 0, "unknown": 0}
    for asset_id, path in cur.fetchall():
        when, source = read(path, tz) or (None, "unknown")
        counts[source] += 1
        cur.execute(
            "UPDATE media.assets SET recorded_at = %s, meta = meta || %s WHERE id = %s",
            (when, db.jsonb({"time_from": source}), asset_id),
        )
    return counts


def overlapping(
    spans: Sequence[tuple[Hashable, float, float]], min_overlap_s: float = 60.0,
) -> list[list[Any]]:
    """Group `(key, start, end)` spans that overlap, directly or through another.

    A camera that stopped and started during one long microphone recording
    makes several files that never overlap each other and all overlap the
    microphone. They are one group. Groups of one are left out.
    """
    ordered = sorted(spans, key=lambda span: (span[1], span[2]))
    group = {key: key for key, _, _ in ordered}

    def root(key: Hashable) -> Hashable:
        while group[key] != key:
            group[key] = group[group[key]]
            key = group[key]
        return key

    for index, (key, _, end) in enumerate(ordered):
        for other, other_start, other_end in ordered[index + 1:]:
            if other_start >= end:
                break
            if min(end, other_end) - other_start >= min_overlap_s:
                group[root(other)] = root(key)
    found: dict[Hashable, list[Any]] = {}
    for key, _, _ in ordered:
        found.setdefault(root(key), []).append(key)
    return [keys for keys in found.values() if len(keys) > 1]


def start(
    clocks: Sequence[tuple[datetime, float]], *, max_spread_s: float = 5.0,
) -> tuple[datetime, float] | None:
    """When take time zero was, by the clocks of recordings already placed.

    Each `(recorded_at, offset_s)` is one device's opinion. At least two must
    agree to within `max_spread_s`: one clock cannot vouch for itself, and
    clocks that disagree vouch for nothing. Returns the moment and the spread.
    """
    if len(clocks) < 2:
        return None
    first = min(when for when, _ in clocks)
    opinions = sorted((when - first).total_seconds() - offset for when, offset in clocks)
    spread = opinions[-1] - opinions[0]
    if spread > max_spread_s:
        return None
    return first + timedelta(seconds=median(opinions)), spread


def _plausible(year: int) -> bool:
    return _EARLIEST_YEAR <= year <= datetime.now().year + 1
