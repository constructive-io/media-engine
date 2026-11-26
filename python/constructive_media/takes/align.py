"""How far apart two recordings started, from the words they share.

Everything here answers one question in one direction:

    offset = seconds to add to `other`'s time to reach `ref`'s time

so a sentence heard at 12 s in `other` and at 112 s in `ref` gives +100. That
is the direction a take stores (see the package notes), and nothing in this
package uses the opposite one.

Nothing here touches the database or a file: the functions take the two
transcripts as `(word, start)` lists and can be tested with made-up words.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import exp, lgamma, log
from statistics import median

from .words import Spoken, telling

#: Words in a phrase. Long enough that a repeat is rare, short enough that
#: two transcripts often agree on all of it.
PHRASE = 5


@dataclass(frozen=True)
class Alignment:
    """Where `other` sits against `ref`, and how much says so."""

    #: Seconds to add to `other`'s time to reach `ref`'s time.
    offset_s: float
    #: Shared words that voted for this offset.
    votes: int
    #: `words` when only the vote placed it, `phrases` when phrases refined it.
    method: str = "words"
    #: How far the phrases disagree with each other, when phrases refined it.
    spread_s: float | None = None


def by_words(
    ref: Sequence[Spoken], other: Sequence[Spoken], *,
    bin_s: float = 0.5, min_votes: int = 8, min_words: int = 20, max_repeats: int = 4,
    max_chance: float = 0.001,
) -> Alignment | None:
    """Vote on the offset with every word the two recordings share.

    Each time a word is heard in both, that pair of times votes for a
    difference, and the difference with the most votes wins. A word said often
    on either side would vote for everything, so it does not vote at all. The
    answer is good to about `bin_s`.

    Two long recordings on one subject share words by the thousand, and those
    votes pile up somewhere by chance alone. So the winner must also be a pile
    that chance would build less than `max_chance` of the time, given how many
    votes were cast and how many offsets there were to cast them on.
    """
    ref, other = telling(ref, 4), telling(other, 4)
    if len(ref) < min_words or len(other) < min_words:
        return None
    in_ref, in_other = Counter(w for w, _ in ref), Counter(w for w, _ in other)
    heard_at = defaultdict(list)
    for word, start in other:
        heard_at[word].append(start)
    votes: Counter[int] = Counter()
    for word, start in ref:
        if in_ref[word] > max_repeats or in_other.get(word, 0) > max_repeats:
            continue
        for there in heard_at.get(word, ()):
            votes[round((start - there) / bin_s)] += 1
    if not votes:
        return None
    best = max(votes, key=lambda b: (votes[b], -abs(b)))
    # Speech timing wobbles across a bin's edge, so the bins either side
    # belong to the same answer.
    agreed = sum(votes.get(best + step, 0) for step in (-1, 0, 1))
    if agreed < min_votes:
        return None
    places = max(1.0, ((ref[-1][1] - ref[0][1]) + (other[-1][1] - other[0][1])) / bin_s)
    # Stray votes fall three bins to a pile, and twice as thickly where the
    # recordings overlap most as they do on average.
    stray = 2 * 3 * (sum(votes.values()) - agreed) / places
    if places * _at_least(agreed, stray) > max_chance:
        return None
    return Alignment(best * bin_s, agreed)


def anchors(words: Sequence[Spoken], length: int = PHRASE) -> dict[tuple[str, ...], float]:
    """Each phrase said exactly once, with the time its first word starts.

    Being said once is the whole safeguard. A phrase said twice, matched to
    the wrong one, gives a sure answer that is out by the gap between them, so
    anything said twice is dropped. There are plenty left.
    """
    seen: dict[tuple[str, ...], float | None] = {}
    texts = [word for word, _ in words]
    for index in range(len(words) - length + 1):
        phrase = tuple(texts[index:index + length])
        seen[phrase] = None if phrase in seen else words[index][1]
    return {phrase: start for phrase, start in seen.items() if start is not None}


def by_phrases(
    ref: Sequence[Spoken], other: Sequence[Spoken], near_s: float, *,
    tolerance_s: float = 5.0, min_phrases: int = 8, length: int = PHRASE,
) -> tuple[float, float, int] | None:
    """Measure the offset from phrases both said once: (offset, spread, phrases).

    Only phrases within `tolerance_s` of the offset already believed are used.
    This sharpens an answer; it does not look for one, because across a long
    recording some pair of phrases always agrees by accident.

    The offset is the median, so one phrase that recurs a minute later does
    not drag it. The spread is half the middle half's width: how far the
    phrases disagree, which is all the precision the word timings support.
    """
    in_ref, in_other = anchors(ref, length), anchors(other, length)
    found = sorted(
        delta for phrase in in_ref.keys() & in_other.keys()
        if abs((delta := in_ref[phrase] - in_other[phrase]) - near_s) <= tolerance_s
    )
    if len(found) < min_phrases:
        return None
    lower, upper = found[len(found) // 4], found[(3 * len(found)) // 4]
    return median(found), (upper - lower) / 2, len(found)


def align(ref: Sequence[Spoken], other: Sequence[Spoken], *, min_votes: int = 8) -> Alignment | None:
    """Place `other` against `ref`: a vote to find it, phrases to sharpen it."""
    found = by_words(ref, other, min_votes=min_votes)
    if found is None:
        return None
    sharper = by_phrases(ref, other, found.offset_s)
    if sharper is None:
        return found
    offset, spread, _ = sharper
    return replace(found, offset_s=offset, method="phrases", spread_s=spread)


def _at_least(count: int, mean: float) -> float:
    """The chance of `count` or more where `mean` are expected (Poisson)."""
    if mean <= 0:
        return 0.0
    term = exp(count * log(mean) - mean - lgamma(count + 1))
    total, step = 0.0, count
    while term > total * 1e-12:
        total += term
        step += 1
        term *= mean / step
    return total
