"""Transcription: what was said and when, apart from what heard it.

A `Transcriber` is given a recording and returns its speech as segments, each
with its words and their times. `clean` then removes what no recogniser should
be believed about, whichever one it was.
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings

#: The shortest a word may be, and the longest a word with no usable end time
#: is assumed to last.
SHORTEST_WORD_S = 0.02
ASSUMED_WORD_S = 0.30

#: The same line this many times running, and at least this many words long, is
#: a recogniser stuck in a loop rather than a person repeating themselves.
LOOP_RUNS = 3
LOOP_WORDS = 3

#: What ends a sentence, what may follow it before the space, and the short
#: words that end in a full stop without ending anything.
SENTENCE_ENDS = ".?!…。？！"
_CLOSERS = "\"'”’)]»"
_ABBREVIATIONS = frozenset("mr mrs ms dr prof st vs etc inc ltd jr sr no".split())
#: A run of words with no sentence end in it is broken, at its longest pause,
#: once it is longer than this.
LONGEST_SENTENCE_WORDS = 40

#: How much of a segment's vocabulary must come from the prompt, and how few
#: of its words may be grammar, for it to be the prompt read back.
ECHO_OVERLAP = 0.6
ECHO_GRAMMAR = 0.25

_FRAMING = frozenset(
    "this recording mentions and the a of in is are it that we you to for with "
    "about video audio clip features includes glossary vocabulary".split()
)
_GRAMMAR = frozenset(
    "i you we he she they it at on in for with to of the a an is was are were be "
    "been have has had do does did so but because when where why how my our their".split()
)


class TranscriptionError(RuntimeError):
    """A recording could not be transcribed."""


@dataclass(frozen=True)
class Word:
    """One spoken word and when it was said, in seconds from the start.

    `probability` is how sure the recogniser was of the word, from 0 to 1, or
    None when it does not say.
    """

    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class Segment:
    """A run of speech: a sentence or so.

    `avg_logprob` is how sure the recogniser was, as the mean log-probability
    of what it wrote: near 0 is certain, below -1 is a guess. `no_speech_prob`
    is its own estimate that nobody was speaking. Either is None when the
    recogniser does not say.
    """

    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    @property
    def compression_ratio(self) -> float | None:
        """How repetitive the text is. Speech sits near 1.5; a recogniser
        stuck in a loop goes well past 2.4."""
        raw = self.text.encode()
        return len(raw) / len(zlib.compress(raw)) if raw else None


@runtime_checkable
class Transcriber(Protocol):
    """Something that can turn a recording into timed words."""

    name: str
    #: What is recorded beside each segment, so transcripts can be told apart.
    model: str

    def transcribe(
        self, audio: Path, *, language: str = "en", prompt: str | None = None,
    ) -> list[Segment]:
        """The speech in `audio`, in order.

        `audio` is any file ffmpeg can read, with or without a picture.
        `prompt` is vocabulary to listen for: a bare list of terms.
        """
        ...


def transcriber(name: str | None = None, *, settings: Settings | None = None) -> Transcriber:
    """The chosen transcriber, or the configured default."""
    return adapters.get("transcription", name, settings=settings)


def clean(segments: Iterable[Segment], prompt: str | None = None) -> list[Segment]:
    """The segments worth keeping: one a sentence, every word a usable length.

    A recogniser's segments are whatever it felt like: a phrase, or half a
    minute. They are cut into sentences first, so a segment means the same
    thing whichever recogniser wrote it. Then, because none of it was said,
    these are dropped:

    - segments with no text, and captions for sounds, like "[MUSIC]";
    - the vocabulary prompt read back, which is what a recogniser does over
      silence;
    - a line repeated word for word, over and over, which is a recogniser
      stuck in a loop. The first of the run is kept.
    """
    kept: list[Segment] = []
    run = 0
    for segment in (sentence for whole in segments for sentence in sentences(whole)):
        text = segment.text.strip()
        if not text or is_caption(text) or (prompt and echoes(text, prompt)):
            continue
        run = run + 1 if kept and kept[-1].text == text and len(text.split()) >= LOOP_WORDS else 0
        if run >= LOOP_RUNS - 1:
            if run == LOOP_RUNS - 1:
                del kept[-(LOOP_RUNS - 2):]  # the repeats let through before it was a loop
            continue
        kept.append(replace(segment, text=text, words=_timed(segment.words)))
    return kept


def sentences(segment: Segment) -> list[Segment]:
    """A segment as one segment for each sentence in it.

    The words decide where each sentence starts and stops, so a segment with
    no words is returned as it is. A sentence takes its sureness from its own
    words when the recogniser gave one for each.
    """
    words = [word for word in segment.words if word.text.strip()]
    parts: list[list[Word]] = [[]]
    for index, word in enumerate(words):
        parts[-1].append(word)
        if index + 1 < len(words) and _ends_sentence(word.text, words[index + 1].text):
            parts.append([])
    parts = [piece for part in parts for piece in _shortened(part)]
    if len(parts) < 2:
        return [segment]
    joiner = " " if " " in segment.text.strip() else ""
    return [
        replace(
            segment, start=part[0].start, end=max(part[0].start, part[-1].end),
            text=joiner.join(word.text.strip() for word in part), words=tuple(part),
            avg_logprob=_sureness(part, segment.avg_logprob),
        )
        for part in parts
    ]


def _ends_sentence(word: str, following: str) -> bool:
    """Whether a sentence ends at `word`, given the word after it."""
    stem = word.strip().rstrip(_CLOSERS)
    if not stem or stem[-1] not in SENTENCE_ENDS:
        return False
    if following.strip().lstrip(_CLOSERS + "“‘([«")[:1].islower():
        return False  # "e.g. this", "3.5 percent", "wait... what"
    if stem[-1] == ".":
        bare = stem.rstrip(".").lower()
        return len(bare) > 1 and bare not in _ABBREVIATIONS
    return True


def _shortened(words: list[Word]) -> list[list[Word]]:
    """Words in runs no longer than a sentence may be, broken at the longest pauses."""
    if len(words) <= LONGEST_SENTENCE_WORDS:
        return [words] if words else []
    middle = len(words) / 2
    at = max(
        range(1, len(words)),
        key=lambda index: (round(words[index].start - words[index - 1].end, 2), -abs(index - middle)),
    )
    return _shortened(words[:at]) + _shortened(words[at:])


def _sureness(words: list[Word], otherwise: float | None) -> float | None:
    if any(word.probability is None for word in words):
        return otherwise
    return sum(math.log(max(word.probability, 1e-9)) for word in words) / len(words)


def is_caption(text: str) -> bool:
    """Whether `text` describes a sound instead of quoting speech: "[MUSIC]",
    "(applause)", "*laughs*", or bare music notes."""
    return re.fullmatch(r"(\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|[♪♫♬\s]+)[.!?\s]*", text) is not None


def echoes(text: str, prompt: str) -> bool:
    """Whether `text` is the prompt read back instead of something said.

    Vocabulary alone cannot decide it: a sentence about the project is made of
    the project's terms. The difference is grammar. A prompt is a list, and
    speech is a sentence, so an echo is text made of the prompt's words with
    almost none of the small words a sentence needs.
    """
    said = re.findall(r"[a-z']+", text.lower())
    content = {word for word in said if len(word) > 2 and word not in _FRAMING}
    listed = {word for word in re.findall(r"[a-z']+", prompt.lower()) if len(word) > 2}
    if len(content) < 2 or len(content & listed) / len(content) < ECHO_OVERLAP:
        return False
    return sum(word in _GRAMMAR for word in said) / len(said) < ECHO_GRAMMAR


def _timed(words: tuple[Word, ...]) -> tuple[Word, ...]:
    """Words with text, each ending after it starts.

    A word whose end is not after its start is given a short assumed length,
    stopping where the next word begins.
    """
    spoken = [replace(word, text=word.text.strip()) for word in words if word.text.strip()]
    timed = []
    for index, word in enumerate(spoken):
        if word.end <= word.start:
            limit = word.start + ASSUMED_WORD_S
            if index + 1 < len(spoken):
                limit = min(limit, spoken[index + 1].start)
            word = replace(word, end=max(word.start + SHORTEST_WORD_S, limit))
        timed.append(word)
    return tuple(timed)
