"""Place every recording of a take on the take's timeline, and prove it.

The order is the order of trust:

1. **Words.** Every pair of recordings that share enough speech is aligned,
   and each recording is reached from the primary through the strongest
   evidence there is. A microphone that shares nothing with the camera but a
   great deal with another microphone is placed through that microphone.
2. **The check.** Each alignment is tested by content before it is used, and
   each placing after: at the same moment, are the same things said? What
   fails is thrown out, and the next strongest evidence is tried. This is
   the step that catches an answer which is confident and wrong.
3. **Sound.** What words could not place is tried by loudness against the
   primary, and accepted only when the match stands far out.
4. **Clocks.** When the recordings already placed show that the devices'
   clocks agree, a clock may place what is left.

An offset a person set is never moved, and is built on like the primary. A
placing found on an earlier run is only forgotten when the check says it is
wrong, so running this without sound decoding does not undo what sound found.

`solve` decides and touches nothing. `sync` reads, solves and stores.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from . import Member, Take, TakeError, clock, find, listing, loudness, members
from .align import Alignment, align
from .check import MISMATCH, OK, Verdict, either
from .words import Spoken, spoken

#: Places one recording against another by sound: `(ref, other)`.
Listener = Callable[[Member, Member], "loudness.Heard | None"]


@dataclass(frozen=True)
class Placement:
    """Where one recording belongs in its take, and on what grounds."""

    member: Member
    #: Seconds to add to the recording's own time to reach take time.
    offset_s: float = 0.0
    confidence: float | None = None
    #: How it was placed; None when nothing could place it.
    method: str | None = None
    #: What the content check made of it; None when there was nothing to check.
    verdict: Verdict | None = None

    @property
    def placed(self) -> bool:
        return self.member.is_primary or self.method is not None

    @property
    def state(self) -> str:
        """One word for a report."""
        if self.member.is_primary:
            return "primary"
        if not self.placed:
            return "unplaced"
        return self.verdict.state if self.verdict else "unchecked"


@dataclass(frozen=True)
class Solved:
    placements: list[Placement]
    #: When take time zero was, if the devices' clocks agree on it.
    started_at: datetime | None = None


def solve(
    recordings: Sequence[Member], said: Mapping[Any, Sequence[Spoken]], *,
    listen: Listener | None = None, min_votes: int = 8,
) -> Solved:
    """Decide where each recording of one take belongs."""
    primary = next((m for m in recordings if m.is_primary), None)
    if primary is None:
        raise TakeError("the take has no primary recording to measure the others against")
    by_id = {m.asset_id: m for m in recordings}

    def judge(member: Member, offset: float, against: Member = primary, against_offset: float = 0.0) -> Verdict:
        return either(
            said.get(against.asset_id, ()), said.get(member.asset_id, ()), offset - against_offset,
            one_duration_s=against.duration_s, other_duration_s=member.duration_s,
        )

    found = {primary.asset_id: Placement(primary)}
    for member in recordings:
        if member.method == "manual" and not member.is_primary:
            found[member.asset_id] = Placement(
                member, member.offset_s, member.confidence, "manual", judge(member, member.offset_s))

    # 1 and 2: words, and the check on each before it is built on.
    def prove(near: Any, far: Any, link: Alignment, offset: float) -> Verdict | None:
        """What the check makes of placing `far` through `near`; None if it fails."""
        member = by_id[far]
        closer = judge(member, link.offset_s, by_id[near])
        if closer.state == MISMATCH:
            return None
        if near == primary.asset_id:
            return closer
        verdict = judge(member, offset)
        if verdict.state == MISMATCH:
            return None
        # With nothing in common with the primary to compare, the recording
        # it was placed through speaks for it.
        return verdict if verdict.decided or not closer.decided else closer

    reached = _reach({key: p.offset_s for key, p in found.items()},
                     _links(recordings, said, min_votes), prove)
    for key, (offset, votes, method, verdict) in reached.items():
        found[key] = Placement(by_id[key], offset, float(votes), method, verdict)

    # 3: sound.
    if listen is not None and primary.has_audio:
        for member in recordings:
            if member.asset_id in found or not member.has_audio:
                continue
            heard = listen(primary, member)
            if heard is None or not heard.sure:
                continue
            verdict = judge(member, heard.offset_s)
            if verdict.state != MISMATCH:
                found[member.asset_id] = Placement(
                    member, heard.offset_s, round(heard.standout, 1), "loudness", verdict)

    # 4: clocks, vouched for by what is already placed and checked.
    vouching = [
        (p.member.recorded_at, p.offset_s) for p in found.values()
        if p.member.recorded_at and (p.member.is_primary or (p.verdict and p.verdict.state == OK))
    ]
    began = clock.start(vouching)
    if began is not None:
        started_at, spread = began
        for member in recordings:
            if member.asset_id in found or member.recorded_at is None:
                continue
            offset = (member.recorded_at - started_at).total_seconds()
            if primary.duration_s and (offset > primary.duration_s or offset + (member.duration_s or 0) < 0):
                continue    # by its own clock it was not rolling during the primary
            verdict = judge(member, offset)
            if verdict.state != MISMATCH:
                found[member.asset_id] = Placement(
                    member, round(offset, 3), round(1 / max(spread, 0.1), 2), "clock", verdict)

    # What nothing placed this time keeps an earlier placing unless it is wrong.
    for member in recordings:
        if member.asset_id in found:
            continue
        kept = Placement(member)
        if member.method is not None:
            verdict = judge(member, member.offset_s)
            if verdict.state != MISMATCH:
                kept = Placement(member, member.offset_s, member.confidence, member.method, verdict)
        found[member.asset_id] = kept
    return Solved([found[m.asset_id] for m in recordings], began[0] if began else None)


def verify(recordings: Sequence[Member], said: Mapping[Any, Sequence[Spoken]]) -> list[Placement]:
    """Check the offsets a take already has, changing nothing."""
    primary = next((m for m in recordings if m.is_primary), None)
    checked = []
    for member in recordings:
        verdict = None
        if primary is not None and member.placed and not member.is_primary:
            verdict = either(
                said.get(primary.asset_id, ()), said.get(member.asset_id, ()), member.offset_s,
                one_duration_s=primary.duration_s, other_duration_s=member.duration_s,
            )
        checked.append(Placement(member, member.offset_s, member.confidence, member.method, verdict))
    return checked


def store(take: Take, solved: Solved) -> int:
    """Write the placings to the take and return how many changed."""
    cur = db.cursor()
    changed = 0
    for placement in solved.placements:
        member = placement.member
        if member.is_primary or member.method == "manual":
            continue
        after = (placement.offset_s, placement.confidence, placement.method)
        if after == (member.offset_s, member.confidence, member.method):
            continue
        cur.execute(
            """UPDATE media.take_assets SET offset_s = %s, offset_confidence = %s, offset_method = %s
                WHERE take_id = %s AND asset_id = %s""",
            (*after, take.id, member.asset_id),
        )
        changed += 1
    if solved.started_at is not None:
        cur.execute("UPDATE media.takes SET started_at = %s WHERE id = %s", (solved.started_at, take.id))
    return changed


def chosen(names: Sequence[str] = (), project: str | None = None) -> list[Take]:
    """The takes named, or every take of the project that has company in it."""
    if names:
        return [find(name) for name in names]
    return [take for take in listing(project) if take.members > 1]


def sync(take: Take, *, by_sound: bool = True, write: bool = True) -> Solved:
    """Place every recording of one take, and store the result unless told not to."""
    recordings = members(take.id)
    said = spoken(m.asset_id for m in recordings)
    solved = solve(recordings, said, listen=listener() if by_sound else None)
    if write:
        store(take, solved)
    return solved


def recheck(take: Take) -> list[Placement]:
    """Check one take's stored offsets by content."""
    recordings = members(take.id)
    return verify(recordings, spoken(m.asset_id for m in recordings))


def can_listen() -> bool:
    """Whether placing by sound is possible here: it needs numpy."""
    return importlib.util.find_spec("numpy") is not None


def listener() -> Listener | None:
    """A `Listener` that reads each file once, or None when sound cannot be read."""
    if not can_listen():
        return None
    shapes: dict[str, Any] = {}

    def listen(ref: Member, other: Member) -> loudness.Heard | None:
        for member in (ref, other):
            if member.source not in shapes:
                there = Path(member.source).is_file()
                shapes[member.source] = loudness.rises(member.source) if there else None
        return loudness.offset(shapes[ref.source], shapes[other.source])

    return listen


def _links(
    recordings: Sequence[Member], said: Mapping[Any, Sequence[Spoken]], min_votes: int,
) -> dict[Any, dict[Any, Alignment]]:
    """Every pair of recordings the words can align, readable from either end.

    `links[a][b].offset_s` is the seconds to add to b's time to reach a's.
    """
    links: dict[Any, dict[Any, Alignment]] = {}
    talking = [m.asset_id for m in recordings if said.get(m.asset_id)]
    for index, one in enumerate(talking):
        for other in talking[index + 1:]:
            found = align(said[one], said[other], min_votes=min_votes)
            if found is not None:
                links.setdefault(one, {})[other] = found
                links.setdefault(other, {})[one] = replace(found, offset_s=-found.offset_s)
    return links


def _reach(
    fixed: Mapping[Any, float], links: Mapping[Any, Mapping[Any, Alignment]],
    prove: Callable[[Any, Any, Alignment, float], Verdict | None],
) -> dict[Any, tuple[float, int, str, Verdict]]:
    """Place what the links can reach from the recordings already fixed.

    The strongest link out of what is placed is always taken next, so every
    recording is reached by the best evidence available. `prove` is asked
    about each placing before anything is built on it, and one it rejects is
    passed over for the next strongest. A chain is as strong as its weakest
    link, and as precise as its least precise one. Returns
    `{recording: (offset, votes, method, verdict)}` in the order placed.
    """
    offsets = dict(fixed)
    strength: dict[Any, float] = {key: float("inf") for key in fixed}
    precise = {key: True for key in fixed}
    reached: dict[Any, tuple[float, int, str, Verdict]] = {}
    broken: set[tuple[Any, Any]] = set()
    while True:
        best = None
        for near in offsets:
            for far, link in links.get(near, {}).items():
                if far in offsets or (near, far) in broken:
                    continue
                if best is None or link.votes > best[2].votes:
                    best = (near, far, link)
        if best is None:
            return reached
        near, far, link = best
        offset = round(offsets[near] + link.offset_s, 3)
        verdict = prove(near, far, link, offset)
        if verdict is None:
            broken.add((near, far))
            continue
        offsets[far] = offset
        strength[far] = min(strength[near], link.votes)
        precise[far] = precise[near] and link.method == "phrases"
        reached[far] = (offset, int(strength[far]), "phrases" if precise[far] else "words", verdict)
