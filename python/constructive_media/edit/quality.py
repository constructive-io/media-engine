"""Whether a passage is worth hearing, judged from its words alone.

A passage can be the right length, start and end on a silence, and still be
someone trailing off, saying one thing three times, or a recogniser guessing
at noise. Each of those leaves a mark in the text, and the marks are what is
measured here. None of it needs a model, so it can run over every candidate
there is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .said import Line, fillers, tokens

#: Words a finished sentence does not end on.
DANGLING = frozenset("""
    a about ago an and are as at because but for her his if in is just like my
    of on or our really so than that the their then to very was were when
    where which who with your
""".split())
#: Words that pick up a sentence someone else, or an earlier moment, began.
LEADING = frozenset({"and", "but", "so", "because", "or", "then", "which"})
#: The gap recorded when nothing is said before, or after, at all.
FAR = 999.0


@dataclass(frozen=True)
class Quality:
    #: Ends on a full stop, and not on a word that leaves a thought hanging.
    ends_complete: bool
    #: Begins where a sentence begins.
    starts_complete: bool
    #: Seconds of quiet after the passage. Near zero means the speaker was
    #: still going and has been cut off.
    tail_gap_s: float
    head_gap_s: float
    #: Share of four-word runs that are said more than once.
    repetition: float
    #: Share of sentences of three words or fewer.
    fragment_ratio: float
    #: Distinct words over words.
    lexical_variety: float
    mean_sentence_w: float
    #: All of the above as one figure from 0 to 1.
    coherence: float


def certainty(logprob: float | None, no_speech: float | None = None) -> float:
    """How far a recogniser's own account of its words can be trusted, 0 to 1.

    1.0 when it gave no account: silence about confidence is not doubt.
    """
    sure = 1.0 if logprob is None else max(0.0, min(1.0, (logprob + 1.0) / 0.75))
    return sure if no_speech is None else sure * (1.0 - min(0.9, no_speech))


def recognised(lines: Iterable[Line], start: float, end: float) -> tuple[float, float] | None:
    """How sure the recogniser was of a stretch, on the recording that heard it best.

    A microphone across the room is unsure of words the one on the collar had
    no trouble with. The cut will use the good one, so the good one is judged.
    Each line counts for the time it spends inside the stretch. The answer is
    the certainty and the confidence it was worked out from, or None when the
    recogniser said nothing about any of it.
    """
    heard: dict[object, list[float]] = {}
    for line in lines:
        inside = min(line.end, end) - max(line.start, start)
        if inside > 0 and line.logprob is not None:
            totals = heard.setdefault(line.asset_id, [0.0, 0.0, 0.0])
            totals[0] += inside
            totals[1] += inside * line.logprob
            totals[2] = max(totals[2], line.no_speech or 0.0)
    return max(((certainty(weighed / inside, no_speech), weighed / inside)
                for inside, weighed, no_speech in heard.values()), default=None)


def confidence(lines: Iterable[Line], start: float, end: float) -> float:
    """The certainty from `recognised`, and 1.0 where there is none to be had."""
    found = recognised(lines, start, end)
    return 1.0 if found is None else found[0]


def judge(
    text: str, *, confidence: float = 1.0, head_gap_s: float = FAR, tail_gap_s: float = FAR,
) -> Quality:
    """Measure one passage's text."""
    text = " ".join(text.split())
    words = tokens(text)
    sentences = [sentence.split() for sentence in re.split(r"[.!?]+", text) if sentence.strip()]

    ends_complete = text[-1:] in (".", "!", "?") and bool(words) and words[-1] not in DANGLING
    # A recogniser writes a capital where a sentence starts, so text that
    # begins in lower case began mid-sentence, whatever its first word is.
    starts_complete = bool(words) and not text[0].islower() and words[0] not in LEADING

    runs = [tuple(words[at:at + 4]) for at in range(len(words) - 3)]
    repetition = 1.0 - len(set(runs)) / len(runs) if runs else 0.0
    fragment_ratio = sum(1 for s in sentences if len(s) <= 3) / len(sentences) if sentences else 1.0
    variety = len(set(words)) / len(words) if words else 0.0
    mean_sentence_w = sum(map(len, sentences)) / len(sentences) if sentences else 0.0
    filler_ratio = fillers(words) / max(len(words), 1)

    coherence = (
        0.24 * (1.0 - min(1.0, repetition * 2.0))
        + 0.18 * (1.0 - min(1.0, fragment_ratio * 1.5))
        + 0.16 * min(1.0, mean_sentence_w / 12.0)
        + 0.10 * min(1.0, variety / 0.55)
        + 0.10 * ends_complete
        + 0.06 * (1.0 - min(1.0, filler_ratio * 8.0))
        # Without this a garbled passage scores well: nonsense has variety.
        + 0.16 * confidence
    )
    return Quality(
        ends_complete, starts_complete, round(min(tail_gap_s, FAR), 2), round(min(head_gap_s, FAR), 2),
        round(repetition, 4), round(fragment_ratio, 4), round(variety, 4),
        round(mean_sentence_w, 2), round(coherence, 4),
    )
