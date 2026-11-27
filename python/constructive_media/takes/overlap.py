"""The sentences two recordings of a take both say: the evidence for a sync.

An offset is one number, and a number can be believed or not. What makes it
believable is being shown the same sentence in two recordings, with the time
each says it at: the difference between those two times is the offset, and a
person can read down a list of them and see that they agree.

So each transcribed line of one recording is looked for in the other. Two
devices never write a sentence down identically, so lines are compared by how
alike they are once the small words are dropped, and a line is only looked for
at all if it has three telling words in it: "yes" and "thank you" are said too
often to place anything.

A sentence found by chance, or one said twice, lands at some other distance
and agrees with nothing. The sentences that really are the same moment all
imply the same offset. So the offset the most sentences agree on, to within a
couple of seconds, is the one the recordings share, and the rest are counted
and left out.

This is a different witness from the one `sync` uses. `sync` aligns single
words and checks the result by content; this pairs whole lines by their
starts, which is coarser, to about the tenth of a second a line's start is
known to. It is here to be read, and when it disagrees with a stored offset by
seconds that is worth listening to.

Everything is measured the way the rest of the package measures: an offset is
the seconds to add to the other recording's time to reach the reference's.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
from typing import Any

from .. import db
from . import Member, Take, TakeError, members
from .words import STOP, normalise

#: How alike two lines must be to be taken for the same sentence, 0 to 1.
MIN_ALIKE = 0.55
#: How far apart, in seconds, the offsets two sentences imply may be and still agree.
TOLERANCE_S = 2.0
#: A line with fewer telling words than this is not looked for.
FEWEST_WORDS = 3
#: Fewer agreeing sentences than this are shown, and no offset is rested on them.
FEWEST = 3


@dataclass(frozen=True)
class Sentence:
    """One transcribed line, with the words of it that are compared."""

    start_s: float
    text: str
    key: tuple[str, ...]


@dataclass(frozen=True)
class Pair:
    """One sentence as each of two recordings has it."""

    ref: Sentence
    other: Sentence
    #: 0 to 1.
    alike: float

    @property
    def offset_s(self) -> float:
        """What this sentence alone says the offset is."""
        return self.ref.start_s - self.other.start_s


@dataclass(frozen=True)
class Shared:
    """What two recordings both say, and the offset it implies."""

    #: The sentences that agree, in the order the other recording says them.
    pairs: tuple[Pair, ...] = ()
    #: The offset they agree on, or None with too few to say.
    offset_s: float | None = None
    #: How many of the other recording's sentences were found in the reference at all.
    found: int = 0
    #: How far apart the agreeing sentences' offsets are.
    spread_s: float = 0.0
    #: Why there is no offset, when there is none.
    reason: str = ""


@dataclass(frozen=True)
class Compared:
    """One recording of a take against the reference, beside the offset the take has for it."""

    ref: Member
    other: Member
    #: The stored seconds to add to the other's time to reach the reference's,
    #: or None when either has not been placed.
    stored_s: float | None
    shared: Shared


def key(text: str) -> tuple[str, ...]:
    """The words of a line that say which line it is."""
    words = (normalise(word) for word in text.split())
    return tuple(word for word in words if len(word) >= 3 and word not in STOP)


def sentence(start_s: float, text: str) -> Sentence:
    return Sentence(float(start_s), " ".join(text.split()), key(text))


def sentences(asset_ids: Iterable[Any]) -> dict[Any, list[Sentence]]:
    """Each asset's transcribed lines that have enough in them to look for, in order."""
    wanted = [str(asset_id) for asset_id in asset_ids]
    found: dict[Any, list[Sentence]] = {}
    if not wanted:
        return found
    cur = db.cursor()
    cur.execute(
        """SELECT asset_id, start_s, text FROM media_video.segments
            WHERE asset_id = ANY(%s::uuid[]) ORDER BY asset_id, start_s, idx""",
        (wanted,),
    )
    for asset_id, start, text in cur.fetchall():
        one = sentence(start, text)
        # Kept even when nothing qualifies, so "no transcript" and "nothing to look for" stay apart.
        lines = found.setdefault(asset_id, [])
        if len(one.key) >= FEWEST_WORDS:
            lines.append(one)
    return found


def candidates(ref: Sequence[Sentence], other: Sequence[Sentence], *, min_alike: float = MIN_ALIKE) -> list[Pair]:
    """Every sentence of `other` beside each sentence of `ref` it is alike enough to be.

    A sentence said three times is paired with all three: which one it is, is
    for `agreeing` to say. Only sentences sharing two telling words are
    compared at all, so an hour against an hour is not a million comparisons.
    """
    holding: dict[str, set[int]] = {}
    for at, one in enumerate(ref):
        for word in set(one.key):
            holding.setdefault(word, set()).add(at)
    pairs = []
    matcher = SequenceMatcher(None, autojunk=False)
    for one in other:
        seen: dict[int, int] = {}
        for word in set(one.key):
            for at in holding.get(word, ()):
                seen[at] = seen.get(at, 0) + 1
        near = sorted(at for at, shared in seen.items() if shared >= 2)
        if not near:
            continue
        matcher.set_seq2(" ".join(one.key))
        for at in near:
            matcher.set_seq1(" ".join(ref[at].key))
            if matcher.real_quick_ratio() < min_alike or matcher.quick_ratio() < min_alike:
                continue
            alike = matcher.ratio()
            if alike >= min_alike:
                pairs.append(Pair(ref[at], one, alike))
    return pairs


def _which(pair: Pair) -> tuple[float, str]:
    # Which of the other recording's sentences a pairing is of.
    return pair.other.start_s, pair.other.text


def agreeing(pairs: Sequence[Pair], *, tolerance_s: float = TOLERANCE_S) -> Shared:
    """The offset the most sentences agree on, and the sentences that agree on it."""
    if not pairs:
        return Shared(reason="they have no sentence in common")
    found = len({_which(pair) for pair in pairs})
    ordered = sorted(pairs, key=lambda pair: pair.offset_s)
    best: tuple[int, float, int, int] | None = None
    first = 0
    for last in range(len(ordered)):
        while ordered[last].offset_s - ordered[first].offset_s > tolerance_s:
            first += 1
        said = len({_which(pair) for pair in ordered[first:last + 1]})
        # The most sentences, and among equals the ones that agree most closely.
        rank = (said, -(ordered[last].offset_s - ordered[first].offset_s))
        if best is None or rank > (best[0], best[1]):
            best = (said, rank[1], first, last)
    assert best is not None
    within = ordered[best[2]:best[3] + 1]
    middle = median(pair.offset_s for pair in within)
    # One place for each sentence: where it is most alike, then nearest the rest.
    chosen: dict[tuple[float, str], Pair] = {}
    for pair in within:
        now = chosen.get(_which(pair))
        if now is None or (pair.alike, -abs(pair.offset_s - middle)) > (now.alike, -abs(now.offset_s - middle)):
            chosen[_which(pair)] = pair
    kept = tuple(sorted(chosen.values(), key=lambda pair: pair.other.start_s))
    offsets = [pair.offset_s for pair in kept]
    spread = max(offsets) - min(offsets)
    if len(kept) < FEWEST:
        only = "one sentence agrees" if len(kept) == 1 else f"{len(kept)} sentences agree"
        return Shared(kept, None, found, spread, f"only {only}, too few to rest an offset on")
    return Shared(kept, float(median(offsets)), found, spread)


def shared(ref: Sequence[Sentence], other: Sequence[Sentence], *, min_alike: float = MIN_ALIKE,
           tolerance_s: float = TOLERANCE_S) -> Shared:
    """What two recordings both say."""
    return agreeing(candidates(ref, other, min_alike=min_alike), tolerance_s=tolerance_s)


def compare(take: Take, *, against: Member | None = None, only: Member | None = None,
            min_alike: float = MIN_ALIKE, tolerance_s: float = TOLERANCE_S) -> list[Compared]:
    """Each recording of a take against its primary, or against `against`; with `only`, just that one."""
    inside = members(take.id)
    ref = against or next((m for m in inside if m.is_primary), None)
    if ref is None:
        raise TakeError(f"take {take.name!r} has no primary; say which recording to compare with, using --against")
    others = [m for m in inside if m.asset_id != ref.asset_id and (only is None or m.asset_id == only.asset_id)]
    if only is not None and only.asset_id == ref.asset_id:
        raise TakeError(f"{ref.label} is what the others are compared with; name a different recording")
    said = sentences([ref.asset_id, *(m.asset_id for m in others)])
    compared = []
    for other in others:
        stored = other.offset_s - ref.offset_s if other.placed and ref.placed else None
        missing = next((m for m in (ref, other) if m.asset_id not in said), None)
        if missing is not None:
            found = Shared(reason=f"{missing.label} has no transcript")
        else:
            found = shared(said[ref.asset_id], said[other.asset_id], min_alike=min_alike, tolerance_s=tolerance_s)
        compared.append(Compared(ref, other, stored, found))
    return compared
