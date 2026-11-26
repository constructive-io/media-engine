"""What was said in a take, on the take's timeline.

Each recording in a take has its own transcript in its own seconds. Here they
are read together, moved onto take time, and each line is given the best
answer there is to who said it.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from .. import db
from ..takes.words import STOP, normalise

#: One word as it was heard: when it starts, when it ends, and how it was written.
Heard = tuple[float, float, str]

#: Sounds made while thinking. Each is one word and comes out cleanly.
FILLERS = frozenset({"um", "uh", "erm", "uhm", "mm", "hmm", "ah", "er"})
#: Phrases that often carry nothing. Often, not always: "you know" is also
#: just two words, so these are looked at rather than cut.
HEDGES = (("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of"), ("like", "i", "said"))


@dataclass(frozen=True)
class Line:
    """One line of a transcript, in take time."""

    start: float
    end: float
    text: str
    #: A name once the voice has one, else the recording and its voice, else
    #: the recording alone.
    who: str
    asset_id: Any
    segment_id: Any = None
    #: How sure the recogniser was: near 0 is certain, below -1 is a guess.
    logprob: float | None = None
    no_speech: float | None = None
    words: tuple[Heard, ...] = ()

    @property
    def length(self) -> float:
        return self.end - self.start


class Span(Protocol):
    """Anything that runs from one moment to another."""

    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...


T = TypeVar("T", bound=Span)


class Spans(Generic[T]):
    """Lines, flags or anything else with a start and an end, kept in order.

    Asking every passage about every line of a long take is the square of an
    afternoon's talk. Kept in order, the few worth asking are found at once.
    """

    def __init__(self, things: Iterable[T]) -> None:
        self.things = sorted(things, key=lambda thing: thing.start)
        self.starts = [thing.start for thing in self.things]
        self.longest = max((thing.end - thing.start for thing in self.things), default=0.0)

    def within(self, start: float, end: float) -> list[T]:
        """The ones that share any time with a stretch."""
        first = bisect_left(self.starts, start - self.longest)
        return [thing for thing in self.things[first:bisect_left(self.starts, end)] if thing.end > start]


def tokens(text: str) -> list[str]:
    """The words of a text as they are compared: letters only, case folded."""
    return [word for word in map(normalise, text.replace("’", "'").split()) if word]


def telling(text: str) -> set[str]:
    """The words of a text that say which sentence it is."""
    return {word for word in tokens(text) if len(word) >= 3 and word not in STOP}


def fillers(words: Sequence[str]) -> int:
    """How many fillers and hedges are among these words."""
    found = sum(1 for word in words if word in FILLERS)
    for hedge in HEDGES:
        size = len(hedge)
        found += sum(1 for at in range(len(words) - size + 1) if tuple(words[at:at + size]) == hedge)
    return found


def lines(take_id: Any, *, with_words: bool = False) -> list[Line]:
    """Every line of every placed recording in a take, in order of take time.

    A recording nothing has placed is left out: its offset is a guess, and a
    guess would put its words at the wrong moment.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT s.start_s + ta.offset_s, s.end_s + ta.offset_s, s.text,
                  coalesce(p.name, cp.name,
                           CASE WHEN c.label IS NOT NULL
                                THEN coalesce(ta.label, a.path) || ' ' || c.label END,
                           s.speaker, ta.label, a.path),
                  s.asset_id, s.id, s.avg_logprob, s.no_speech_prob
             FROM media.take_assets ta
             JOIN media.assets a ON a.id = ta.asset_id
             JOIN media_video.segments s ON s.asset_id = ta.asset_id
             LEFT JOIN media_video.speaker_clusters c ON c.id = s.cluster_id
             LEFT JOIN media.personas cp ON cp.id = c.persona_id
             LEFT JOIN media.personas p ON p.id = s.persona_id
            WHERE ta.take_id = %s AND (ta.is_primary OR ta.offset_method IS NOT NULL)
            ORDER BY 1, 2, s.asset_id""",
        (take_id,),
    )
    rows = cur.fetchall()
    heard = _words(take_id) if with_words else {}
    return [
        Line(float(start), float(end), text or "", who, asset_id, segment_id,
             None if logprob is None else float(logprob),
             None if no_speech is None else float(no_speech),
             tuple(heard.get(segment_id, ())))
        for start, end, text, who, asset_id, segment_id, logprob, no_speech in rows
    ]


def _words(take_id: Any) -> dict[Any, list[Heard]]:
    cur = db.cursor()
    cur.execute(
        """SELECT w.segment_id, w.start_s + ta.offset_s, w.end_s + ta.offset_s, w.word
             FROM media.take_assets ta
             JOIN media_video.segments s ON s.asset_id = ta.asset_id
             JOIN media_video.words w ON w.segment_id = s.id
            WHERE ta.take_id = %s AND (ta.is_primary OR ta.offset_method IS NOT NULL)
              AND w.start_s IS NOT NULL AND w.end_s IS NOT NULL
            ORDER BY w.segment_id, w.start_s, w.idx""",
        (take_id,),
    )
    found: dict[Any, list[Heard]] = {}
    for segment_id, start, end, word in cur.fetchall():
        found.setdefault(segment_id, []).append((float(start), float(end), word))
    return found


def same_speech(one: Line, other: Line) -> bool:
    """Whether two lines are one piece of speech, heard by two microphones.

    They are when they cover the same moment, unless what they say has nothing
    in common: two people talking at once are also two lines at one moment,
    and both of those are wanted. Recognisers mishear, so a little in common
    is enough, and lines too short to judge are taken to be the same.
    """
    shared = min(one.end, other.end) - max(one.start, other.start)
    if shared <= 0.5 * min(one.length, other.length):
        return False
    fewer, more = sorted((telling(one.text), telling(other.text)), key=len)
    return len(fewer) < 3 or len(fewer & more) / len(fewer) >= 0.2


def once(heard: Iterable[Line]) -> list[Line]:
    """One line for each thing said, where several microphones each heard it.

    Of the lines that are the same speech, the one with the most text stays:
    the microphone that caught the most of it. Lines from one recording are
    never weighed against each other.
    """
    kept: list[Line] = []
    running: list[int] = []
    for line in sorted(heard, key=lambda each: (each.start, -len(each.text))):
        running = [at for at in running if kept[at].end > line.start]
        twin = next((at for at in running
                     if kept[at].asset_id != line.asset_id and same_speech(kept[at], line)), None)
        if twin is None:
            running.append(len(kept))
            kept.append(line)
        elif len(line.text) > len(kept[twin].text):
            kept[twin] = line
    return sorted(kept, key=lambda each: (each.start, each.end))


def talked_over(told: Sequence[Line]) -> float:
    """Seconds in which two of these lines are said at once.

    Given lines that `once` has been through: two that still share a moment
    are two people talking, not one person heard twice.
    """
    total, running = 0.0, []
    for line in told:
        running = [other for other in running if other.end > line.start]
        for other in running:
            shared = min(line.end, other.end) - line.start
            if other.asset_id != line.asset_id and shared > 0.5 * min(line.length, other.length):
                total += shared
        running.append(line)
    return total
