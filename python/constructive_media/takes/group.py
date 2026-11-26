"""Make takes: recordings that were rolling at the same moment belong together.

Grouping only ever adds. It makes a take, or puts a recording into the one
take its group already has. It never moves a recording, renames a take or
deletes one, so it is safe to run after every ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db
from . import add, clock, create, find, members, next_name

#: Recordings that are something the engine made from other recordings.
_NOT_DERIVED = "NOT EXISTS (SELECT 1 FROM media.derivations d WHERE d.output_id = a.id)"


@dataclass(frozen=True)
class Recording:
    asset_id: Any
    path: str
    medium: str
    duration_s: float
    has_audio: bool
    project: str | None
    #: The take it is already in, if any.
    take: str | None = None

    @property
    def role(self) -> str:
        return "camera" if self.medium == "video" else "lav"


@dataclass
class Proposal:
    """One group of recordings and what to do with it."""

    #: `new` for a take to make, `join` for recordings to add to `take`,
    #: `skip` for a group that is left alone, with the `reason`.
    action: str
    take: str
    #: The recordings to add. The first of a new take is its primary.
    recordings: list[Recording] = field(default_factory=list)
    reason: str = ""


def by_clock(
    project: str | None = None, *, prefix: str = "take", min_overlap_s: float = 60.0,
) -> list[Proposal]:
    """Propose takes from recordings whose clocks say they overlapped."""
    cur = db.cursor()
    cur.execute(
        f"""SELECT a.id, a.path, a.medium, a.duration_s, a.has_audio, a.project,
                   (SELECT min(t.name) FROM media.take_assets ta JOIN media.takes t ON t.id = ta.take_id
                     WHERE ta.asset_id = a.id),
                   (SELECT count(*) FROM media.take_assets ta WHERE ta.asset_id = a.id),
                   extract(epoch FROM a.recorded_at)
              FROM media.assets a
             WHERE a.recorded_at IS NOT NULL AND a.duration_s > 0
               AND a.medium IN ('video', 'audio') AND {_NOT_DERIVED}
               AND (%s::text IS NULL OR a.project = %s)""",
        (project, project),
    )
    known, spans, crowded = {}, [], set()
    for asset_id, path, medium, duration, has_audio, its_project, take, takes, began in cur.fetchall():
        known[asset_id] = Recording(asset_id, path, medium, float(duration), bool(has_audio), its_project, take)
        spans.append((asset_id, float(began), float(began) + float(duration)))
        if takes > 1:
            crowded.add(asset_id)

    proposals, named = [], set()
    for keys in clock.overlapping(spans, min_overlap_s):
        group = sorted((known[key] for key in keys), key=lambda r: r.path)
        homes = sorted({r.take for r in group if r.take})
        fresh = [r for r in group if r.take is None]
        if not fresh:
            continue
        if len(homes) > 1 or any(r.asset_id in crowded for r in group):
            proposals.append(Proposal("skip", ", ".join(homes), fresh,
                                      "these overlap recordings that are in different takes"))
        elif homes:
            proposals.append(Proposal("join", homes[0], fresh))
        else:
            name = next_name(prefix, named)
            named.add(name)
            fresh.sort(key=_primary_first)
            proposals.append(Proposal("new", name, fresh))
    return proposals


def alone(project: str | None = None, *, min_duration_s: float = 5.0) -> list[Proposal]:
    """Propose a take of its own for each transcribed recording in no take.

    A recording nothing else covers is still something to cut from, and
    everything that cuts works on takes. Run this after `by_clock` and
    `match` have had their chance to find it company.
    """
    cur = db.cursor()
    cur.execute(
        f"""SELECT a.id, a.path, a.medium, a.duration_s, a.has_audio, a.project
              FROM media.assets a
             WHERE a.duration_s > %s AND a.medium IN ('video', 'audio') AND {_NOT_DERIVED}
               AND EXISTS (SELECT 1 FROM media_video.segments s WHERE s.asset_id = a.id)
               AND NOT EXISTS (SELECT 1 FROM media.take_assets ta WHERE ta.asset_id = a.id)
               AND (%s::text IS NULL OR a.project = %s)
             ORDER BY a.path""",
        (min_duration_s, project, project),
    )
    recordings = [Recording(*row) for row in cur.fetchall()]
    cur.execute("SELECT name FROM media.takes")
    taken = {name for (name,) in cur.fetchall()}
    proposals = []
    for recording in recordings:
        name = Path(recording.path).stem
        if name in taken:
            name = f"{name}-{str(recording.asset_id)[:6]}"
        taken.add(name)
        proposals.append(Proposal("new", name, [recording]))
    return proposals


def apply(proposals: list[Proposal]) -> int:
    """Carry out the proposals and return how many recordings were placed in takes."""
    added = 0
    for proposal in proposals:
        if proposal.action == "skip":
            continue
        if proposal.action == "new":
            first = proposal.recordings[0]
            take_id = create(proposal.take, first.project)
            used: set[str] = set()
        else:
            take_id = find(proposal.take).id
            used = {member.label for member in members(take_id)}
        for index, recording in enumerate(proposal.recordings):
            label = _label(recording, used)
            used.add(label)
            add(take_id, recording.asset_id, role=recording.role, label=label,
                is_primary=proposal.action == "new" and index == 0)
            added += 1
    return added


def _primary_first(recording: Recording) -> tuple:
    # Picture with sound is what everything else is hung on. Among equals the
    # longest covers the most of the others.
    return (recording.medium != "video", not recording.has_audio, -recording.duration_s, recording.path)


def _label(recording: Recording, used: set[str]) -> str:
    label = Path(recording.path).stem
    return label if label not in used else f"{label}-{str(recording.asset_id)[:6]}"
