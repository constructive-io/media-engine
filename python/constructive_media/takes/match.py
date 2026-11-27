"""Find the camera a sound recording belongs with, by what is said.

Grouping by clock needs clocks. A recorder whose clock was never set, or a
file that passed through something which stripped its time, has only its
content to say where it belongs. Here that content is compared with every
camera's: the camera that shares the most with it is aligned, and the
alignment is then checked like any other. Only a match that passes the check
is offered.

Matching only ever adds a recording to a take. It moves and deletes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import add, create, next_name
from .align import Alignment, by_phrases, by_words
from .check import OK, Verdict, either
from .group import _NOT_DERIVED
from .words import spoken


@dataclass(frozen=True)
class Match:
    """A sound recording, and the camera it was recorded alongside."""

    recording: Any
    recording_path: str
    camera: Any = None
    camera_path: str | None = None
    #: Seconds to add to the recording's time to reach the camera's.
    alignment: Alignment | None = None
    verdict: Verdict | None = None

    @property
    def confirmed(self) -> bool:
        return self.verdict is not None and self.verdict.state == OK


def find(project: str | None = None, *, min_votes: int = 6, min_duration_s: float = 30.0) -> list[Match]:
    """Match each transcribed sound recording that is in no take to a camera."""
    cur = db.cursor()
    cur.execute(
        f"""SELECT a.id, a.path, a.medium, a.duration_s,
                   EXISTS (SELECT 1 FROM media.take_assets ta WHERE ta.asset_id = a.id)
              FROM media.assets a
             WHERE a.medium IN ('video', 'audio') AND a.duration_s >= %s AND {_NOT_DERIVED}
               AND EXISTS (SELECT 1 FROM media_video.segments s WHERE s.asset_id = a.id)
               AND (%s::text IS NULL OR a.project = %s)
             ORDER BY a.path""",
        (min_duration_s, project, project),
    )
    rows = cur.fetchall()
    cameras = [row for row in rows if row[2] == "video"]
    loose = [row for row in rows if row[2] == "audio" and not row[4]]
    if not loose:
        return []
    said = spoken(row[0] for row in cameras + loose)

    matches = []
    for recording, path, _, duration, _ in loose:
        best = None
        for camera, camera_path, _, camera_duration, _ in cameras:
            found = by_words(said.get(camera, ()), said.get(recording, ()), min_votes=min_votes)
            if found and (best is None or found.votes > best[0].votes):
                best = (found, camera, camera_path, camera_duration)
        if best is None:
            matches.append(Match(recording, path))
            continue
        found, camera, camera_path, camera_duration = best
        if sharper := by_phrases(said[camera], said[recording], found.offset_s):
            found = Alignment(sharper[0], found.votes, "phrases", sharper[1])
        verdict = either(said[camera], said[recording], found.offset_s,
                         one_duration_s=camera_duration, other_duration_s=duration)
        matches.append(Match(recording, path, camera, camera_path, found, verdict))
    return matches


def apply(matches: list[Match], *, prefix: str = "take") -> tuple[int, list[tuple[Match, str]]]:
    """Put each confirmed match in its camera's take, making the take if need be.

    Returns how many were added, and the confirmed matches that were not with
    the reason for each.
    """
    cur = db.cursor()
    added, left = 0, []
    for match in matches:
        if not match.confirmed:
            continue
        cur.execute(
            """SELECT ta.take_id, ta.is_primary, ta.offset_s, ta.offset_method, a.project
                 FROM media.assets a
                 LEFT JOIN media.take_assets ta ON ta.asset_id = a.id
                WHERE a.id = %s
                ORDER BY ta.is_primary DESC NULLS LAST""",
            (match.camera,),
        )
        homes = cur.fetchall()
        take_id, is_primary, camera_offset, method, project = homes[0]
        if take_id is None:
            take_id = create(next_name(prefix), project)
            add(take_id, match.camera, role="camera", label=Path(match.camera_path).stem, is_primary=True)
            camera_offset = 0.0
        elif len(homes) > 1:
            left.append((match, "its camera is in more than one take"))
            continue
        elif not is_primary and method is None:
            left.append((match, "its camera has not been placed in its take yet; run `media sync` first"))
            continue
        cur.execute("SELECT label FROM media.take_assets WHERE take_id = %s", (take_id,))
        used = {label for (label,) in cur.fetchall()}
        label = Path(match.recording_path).stem
        if label in used:
            label = f"{label}-{str(match.recording)[:6]}"
        add(take_id, match.recording, role="lav", label=label,
            offset_s=round(camera_offset + match.alignment.offset_s, 3),
            confidence=float(match.alignment.votes), method=match.alignment.method)
        added += 1
    return added, left
