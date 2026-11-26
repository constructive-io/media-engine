"""Prove an offset by content: at the same moment, do both say the same thing?

Every measure a sync has of itself can pass on an offset stored the wrong way
round, because the measurement was right and only its meaning was wrong. What
cannot be fooled is the question the sync exists to answer. So this takes
moments of one recording, finds the same moments in the other through the
offset, and compares the words.

It does not only score the place the offset points at. An offset ten seconds
out still shares half of a twenty-second window. It slides the comparison and
finds where agreement peaks: a right offset peaks at zero, and anything else
says the offset is wrong and roughly by how much.

Read the residual as a size, not a measurement. Shifting a twenty-second
window by a second barely changes the words in it, so the peak sits on a
plateau. Fine corrections come from phrases, in `align`. This is the check for
an answer that is far out, and it is tuned never to cry wolf at one that is
right: on recordings whose offsets were known to a few hundredths of a second
it passed every one, caught every error of seven seconds or more, and nineteen
in twenty at five.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, replace
from statistics import median

from .words import Spoken, telling

OK = "ok"
MISMATCH = "mismatch"
#: Nothing to judge by: too little is said in one of them, or nothing said in
#: one is said anywhere near it in the other.
NOTHING = "nothing-to-compare"
#: One window compared, on a recording that covers little of the other.
TOO_LITTLE = "too-little-overlap"


@dataclass(frozen=True)
class Verdict:
    """What comparing the words says about one offset."""

    state: str
    #: Share of words in common at the offset itself, averaged over windows.
    agreement: float | None = None
    #: Roughly the seconds to add to the offset to put it right. None when
    #: that cannot be told.
    residual_s: float | None = None
    #: The offset is out by more than was searched.
    beyond_search: bool = False
    #: Windows that had enough said in both recordings to compare.
    windows: int = 0

    @property
    def decided(self) -> bool:
        return self.state in (OK, MISMATCH)


class _Said:
    """One transcript's telling words, quick to cut windows from."""

    def __init__(self, words: Sequence[Spoken]) -> None:
        kept = telling(words)
        self.words = [word for word, _ in kept]
        self.times = [start for _, start in kept]

    def window(self, start: float, length: float) -> set[str]:
        return set(self.words[bisect_left(self.times, start):bisect_left(self.times, start + length)])


def check(
    ref: Sequence[Spoken], other: Sequence[Spoken], offset_s: float, *,
    other_duration_s: float | None = None, windows: int = 6, window_s: float = 20.0,
    min_agreement: float = 0.25, min_words: int = 6, search_s: float = 45.0,
    step_s: float = 1.0, max_residual_s: float = 2.0,
) -> Verdict:
    """Judge `offset_s`, the seconds to add to `other`'s time to reach `ref`'s.

    `NOTHING` is a real and common answer, not a soft failure: a microphone
    worn by someone who stays quiet leaves nothing to compare, and a check
    that cries wolf at every quiet guest is a check that gets ignored. It is
    also the answer for an offset so far out that nothing lines up within
    `search_s`, so it means "not shown", never "shown to be right".
    """
    said, heard = _Said(ref), _Said(other)
    if not said.times or not heard.times:
        return Verdict(NOTHING)
    # Probe where the reference has speech. Even spacing in time would spend
    # most windows on silence.
    stride = max(1, len(said.times) // windows)
    probes = said.times[::stride][:windows]
    shifts = [step * step_s for step in range(int(-search_s / step_s), int(search_s / step_s) + 1)]

    at_offset, tops, middles = [], [], []
    for probe in probes:
        wanted = said.window(probe, window_s)
        if len(wanted) < min_words:
            continue
        curve = []
        for shift in shifts:
            there = probe - offset_s + shift
            if there < 0 or (other_duration_s and there > other_duration_s - window_s):
                continue
            found = heard.window(there, window_s)
            if len(found) >= min_words:
                # Against the smaller set, not the union: one microphone
                # catches words the other misses, and that is not disagreement.
                curve.append((len(wanted & found) / min(len(wanted), len(found)), -abs(shift), shift))
        if not curve:
            continue
        best, _, top = max(curve)
        if best < min_agreement:
            continue    # agreement nowhere is no evidence either way
        at_offset.append(next((score for score, _, shift in curve if shift == 0), 0.0))
        # The peak is a plateau: a window moved a few seconds still holds
        # most of its words, and which shift on the plateau scores highest is
        # partly luck. So the peak is read two ways, as its highest point and
        # as its middle. Speech found `shift` later than the offset said means
        # the offset is that much too large.
        plateau = [shift for score, _, shift in curve if score >= 0.9 * best]
        tops.append(-top)
        middles.append(-(plateau[0] + plateau[-1]) / 2)

    if not at_offset:
        return Verdict(NOTHING)
    # One window is not evidence, unless there is one window because the
    # offset is wrong. A short recording misses most probes honestly; a long
    # one that misses them has been sent to the wrong place.
    span = said.times[-1] - said.times[0]
    coverage = other_duration_s / span if other_duration_s and span else 0.0
    if len(at_offset) < 2 and coverage < 0.5:
        return Verdict(TOO_LITTLE, windows=len(at_offset))
    agreement = sum(at_offset) / len(at_offset)
    # The middle window, so one that lands on a repeated phrase does not
    # invent a drift.
    top, residual = median(tops), median(middles)
    # An error beyond the search pins the peak to its edge. That edge is not
    # a measurement and is not reported as one.
    beyond = abs(top) >= search_s - step_s
    # Either reading alone calls a right offset wrong now and then. Both
    # together did not once.
    out = min(abs(top), abs(residual)) > max_residual_s
    good = agreement >= min_agreement and not out
    return Verdict(OK if good else MISMATCH, round(agreement, 3),
                   None if beyond else residual, beyond, len(at_offset))


def either(
    one: Sequence[Spoken], other: Sequence[Spoken], offset_s: float, *,
    one_duration_s: float | None = None, other_duration_s: float | None = None, **options: float,
) -> Verdict:
    """Judge `offset_s`, the seconds to add to `other`'s time to reach `one`'s.

    The same question as `check`, asked from whichever side says less. The
    moments compared are taken from the recording they are asked of first, and
    a two-minute clip inside a two-hour recording is only met by chance when
    the moments come from the long one. Taken from the clip, every one of them
    lands.
    """
    if len(other) <= len(one):
        found = check(other, one, -offset_s, other_duration_s=one_duration_s, **options)
        return found if not found.residual_s else replace(found, residual_s=-found.residual_s)
    return check(one, other, offset_s, other_duration_s=other_duration_s, **options)
