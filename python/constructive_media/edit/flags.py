"""Flags: places in a recording to cut, to look at, or to stay away from.

A flag is a span of one recording, in that recording's own seconds, with what
is wrong there and what to do about it:

- `cut`      it can come out and nothing is lost: a filler, a stutter, dead air
- `review`   it may be nothing: a hedge, a sentence started twice
- `avoid`    do not use it: someone asked for it not to be used, or the
             recogniser was guessing

Flags are suggestions until a person rules on one, and the ruling is kept: a
flag someone has accepted or dismissed is never removed or reopened by a later
scan. Everything else is redone each time, so a scan leaves a recording with
exactly the flags it would have been given from scratch.

One flag is not like the others. Somebody saying "that's off the record" does
not make a passage worse, it makes it unusable, and nothing else can see it:
the passage is clear, well recorded and ranks high. `withheld` is the list of
those, and a ranking leaves them out.
"""

from __future__ import annotations

import uuid
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from .. import db
from ..takes.words import normalise
from .said import FILLERS, HEDGES, Heard, Spans, tokens

NAMESPACE = uuid.UUID("2b7f6a1c-9d4e-5f38-b6a2-71c0e4d95a83")
#: Who these flags are from, among everything that writes flags.
PRODUCER = "flags"
#: The kinds a scan finds, and so the kinds it may take away again.
KINDS = ("off_record", "hallucination", "filler", "repetition", "false_start", "long_pause")

#: Things people say that mean "do not use this".
OFF_RECORD = tuple(tuple(tokens(phrase)) for phrase in (
    "off the record", "don't use that", "do not use that", "cut that", "don't record",
    "not for the video", "delete that",
))
#: Below this the recogniser was guessing at noise.
GUESSING = -1.2
#: Words said twice for emphasis or agreement, which is not a stutter.
EMPHATIC = frozenset({"yeah", "yes", "no", "okay", "ok", "oh", "very", "really", "right", "so", "well",
                      "bye", "ha", "hey", "wow", "now", "never", "please"})
#: Two runs of the same words this close together are one said twice.
AGAIN_S = 0.6
#: The shortest span a flag may have. A flag with no length cannot be cut,
#: played or looked at.
SHORTEST_S = 0.02


@dataclass(frozen=True)
class Flag:
    asset_id: Any
    segment_id: Any
    kind: str
    start_s: float
    end_s: float
    action: str
    #: How much it matters, 0 to 1.
    severity: float
    detail: str

    @property
    def id(self) -> str:
        return flag_id(self.asset_id, self.kind, self.start_s, self.end_s)


@dataclass(frozen=True)
class Spoken:
    """One line of one recording, in the recording's own time, with its words."""

    segment_id: Any
    start: float
    end: float
    text: str
    logprob: float | None
    words: Sequence[Heard]


class Mark(NamedTuple):
    """A flag carried from its recording's time to its take's."""

    start: float
    end: float
    kind: str
    action: str


def flag_id(asset_id: Any, kind: str, start_s: float, end_s: float) -> str:
    """The id of the flag of this kind at this span: found again, it is the same flag."""
    return str(uuid.uuid5(NAMESPACE, f"{asset_id}|{kind}|{float(start_s):.2f}|{float(end_s):.2f}"))


def _has(words: Sequence[str], phrase: Sequence[str]) -> bool:
    size = len(phrase)
    return any(tuple(words[at:at + size]) == tuple(phrase) for at in range(len(words) - size + 1))


def read(asset_id: Any, lines: Sequence[Spoken], *, min_pause_s: float = 1.5,
         talked_through: Any = None) -> Iterator[Flag]:
    """Every flag one recording's transcript gives rise to.

    `talked_through(start, end)` says whether another microphone in the room
    heard talking in a stretch this one heard as quiet. One person's pause is
    often another's turn, and that is not dead air to cut.
    """
    def flag(line: Spoken, kind: str, start: float, end: float, action: str,
             severity: float, detail: str) -> Flag:
        return Flag(asset_id, line.segment_id, kind, start, max(end, start + SHORTEST_S), action,
                    round(min(1.0, severity), 3), detail)

    heard: list[tuple[str, float, float, Spoken]] = []
    for line in lines:
        said = tokens(line.text)
        for phrase in OFF_RECORD:
            if _has(said, phrase):
                yield flag(line, "off_record", line.start, line.end, "avoid", 1.0,
                           f"said: {' '.join(phrase)!r}")
        if line.logprob is not None and line.logprob < GUESSING:
            # Flagged, not removed: the sound underneath may be fine and only
            # the words wrong.
            yield flag(line, "hallucination", line.start, line.end, "avoid", abs(line.logprob) / 3.0,
                       f"the recogniser was guessing (confidence {line.logprob:.2f})")
        heard.extend((word, start, end, line) for start, end, text in line.words
                     if (word := normalise(text.replace("’", "'"))))

    words = [word for word, _, _, _ in heard]
    at = 0
    while at < len(heard):
        word, start, end, line = heard[at]
        if at and start - heard[at - 1][2] >= min_pause_s:
            quiet = (heard[at - 1][2], start)
            if not (talked_through and talked_through(*quiet)):
                yield flag(line, "long_pause", *quiet, "cut", (quiet[1] - quiet[0]) / 4.0,
                           f"{quiet[1] - quiet[0]:.1f}s of silence")
        if word in FILLERS:
            # Worth more the longer it is: a long "uhhh" should go, a quick
            # "uh" inside a sentence is often just how someone talks.
            yield flag(line, "filler", start, end, "cut", 0.4 + (end - start), repr(word))
        for hedge in HEDGES:
            if tuple(words[at:at + len(hedge)]) == hedge:
                yield flag(line, "filler", start, heard[at + len(hedge) - 1][2], "review", 0.3,
                           f"hedge: {' '.join(hedge)!r}")
        size = _again(heard, at)
        if size == 1:
            yield flag(line, "repetition", end, heard[at + 1][2], "cut", 0.5, f"repeated {word!r}")
        elif size:
            # Someone who starts a sentence, stops and starts it again. The
            # first go is what an editor takes out, but a phrase said twice
            # can also be meant, so it is looked at, not cut.
            yield flag(line, "false_start", start, heard[at + size][1], "review", 0.4,
                       f"said twice: {' '.join(words[at:at + size])!r}")
        at += max(size, 1)


def _again(heard: Sequence[tuple[str, float, float, Any]], at: int) -> int:
    """How many words starting here are said again straight away, the longest run first."""
    for size in (4, 3, 2, 1):
        if at + 2 * size > len(heard):
            continue
        first = [word for word, _, _, _ in heard[at:at + size]]
        if (first == [word for word, _, _, _ in heard[at + size:at + 2 * size]]
                and heard[at + size][1] - heard[at + size - 1][2] < AGAIN_S
                and not all(word in EMPHATIC or len(word) < 2 for word in first)):
            return size
    return 0


def scan(match: str | None = None, project: str | None = None, *, min_pause_s: float = 1.5) -> Counter[str]:
    """Flag every transcribed recording, or those chosen, and count what was found."""
    cur = db.cursor()
    cur.execute(
        """SELECT s.asset_id, s.id, s.start_s, s.end_s, s.text, s.avg_logprob
             FROM media_video.segments s
             JOIN media.assets a ON a.id = s.asset_id
            WHERE (%s::text IS NULL OR a.project = %s) AND (%s::text IS NULL OR a.path ~* %s)
            ORDER BY s.asset_id, s.start_s, s.idx""",
        (project, project, match, match),
    )
    segments = cur.fetchall()
    cur.execute(
        """SELECT w.segment_id, w.start_s, w.end_s, w.word
             FROM media_video.words w
             JOIN media_video.segments s ON s.id = w.segment_id
             JOIN media.assets a ON a.id = s.asset_id
            WHERE (%s::text IS NULL OR a.project = %s) AND (%s::text IS NULL OR a.path ~* %s)
              AND w.start_s IS NOT NULL AND w.end_s IS NOT NULL
            ORDER BY w.segment_id, w.start_s, w.idx""",
        (project, project, match, match),
    )
    words: dict[Any, list[Heard]] = {}
    for segment_id, start, end, word in cur.fetchall():
        words.setdefault(segment_id, []).append((float(start), float(end), word))
    recordings: dict[Any, list[Spoken]] = {}
    for asset_id, segment_id, start, end, text, logprob in segments:
        recordings.setdefault(asset_id, []).append(Spoken(
            segment_id, float(start), float(end), text or "",
            None if logprob is None else float(logprob), words.get(segment_id, ())))

    company = _company(recordings)
    found = [flag for asset_id, lines in recordings.items()
             for flag in read(asset_id, lines, min_pause_s=min_pause_s,
                              talked_through=company.get(asset_id))]
    _store(found, list(recordings))
    return Counter(flag.kind for flag in found)


def _company(recordings: dict[Any, list[Spoken]]) -> dict[Any, Any]:
    """For each recording that shares a take, a test of whether the others heard talking."""
    wanted = [str(asset_id) for asset_id in recordings]
    cur = db.cursor()
    cur.execute(
        """SELECT mine.asset_id, other.asset_id, mine.offset_s - other.offset_s
             FROM media.take_assets mine
             JOIN media.take_assets other ON other.take_id = mine.take_id AND other.asset_id <> mine.asset_id
            WHERE mine.asset_id = ANY(%s::uuid[])
              AND (mine.is_primary OR mine.offset_method IS NOT NULL)
              AND (other.is_primary OR other.offset_method IS NOT NULL)""",
        (wanted,),
    )
    beside: dict[Any, list[tuple[Any, float]]] = {}
    for mine, other, shift in cur.fetchall():
        beside.setdefault(mine, []).append((other, float(shift)))
    # When each word of each of the others starts. Most of them are being
    # scanned too and are already read.
    others = {other for found in beside.values() for other, _ in found}
    starts = {asset_id: sorted(start for line in recordings[asset_id] for start, _, _ in line.words)
              for asset_id in others & recordings.keys()}
    if unread := others - recordings.keys():
        cur.execute(
            """SELECT s.asset_id, w.start_s
                 FROM media_video.words w JOIN media_video.segments s ON s.id = w.segment_id
                WHERE s.asset_id = ANY(%s::uuid[]) AND w.start_s IS NOT NULL
                ORDER BY s.asset_id, w.start_s""",
            ([str(asset_id) for asset_id in unread],),
        )
        for asset_id, start in cur.fetchall():
            starts.setdefault(asset_id, []).append(float(start))

    def test(others: list[tuple[Any, float]]) -> Any:
        def talked_through(start: float, end: float, margin: float = 0.2) -> bool:
            # Two words or more, clear of the edges: one stray word is as
            # likely a recogniser's slip as someone speaking.
            return any(
                bisect_left(times, end + shift - margin) - bisect_right(times, start + shift + margin) >= 2
                for other, shift in others if (times := starts.get(other))
            )
        return talked_through

    return {asset_id: test(others) for asset_id, others in beside.items()}


def _store(found: Sequence[Flag], scanned: Sequence[Any]) -> None:
    rows = {}
    for flag in found:
        key = flag.id
        rows[key] = {
            "id": key, "segment_id": str(flag.segment_id), "asset_id": str(flag.asset_id),
            "kind": flag.kind, "start_s": flag.start_s, "end_s": flag.end_s, "action": flag.action,
            "severity": flag.severity, "detail": flag.detail,
        }
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            """INSERT INTO media_video.flags
                      (id, subject_type, subject_id, asset_id, kind, start_s, end_s,
                       action, severity, detail, producer)
               SELECT f.id, 'segment', f.segment_id, f.asset_id, f.kind, f.start_s, f.end_s,
                      f.action, f.severity, f.detail, %s
                 FROM jsonb_to_recordset(%s) AS f(
                      id uuid, segment_id uuid, asset_id uuid, kind text, start_s double precision,
                      end_s double precision, action text, severity double precision, detail text)
               ON CONFLICT (id) DO UPDATE SET
                 subject_id = EXCLUDED.subject_id, action = EXCLUDED.action,
                 severity = EXCLUDED.severity, detail = EXCLUDED.detail
               WHERE (flags.subject_id, flags.action, flags.severity, flags.detail) IS DISTINCT FROM
                     (EXCLUDED.subject_id, EXCLUDED.action, EXCLUDED.severity, EXCLUDED.detail)""",
            (PRODUCER, db.jsonb(list(rows.values()))),
        )
        cur.execute(
            """DELETE FROM media_video.flags f
                WHERE f.producer = %s AND f.kind = ANY(%s) AND f.accepted IS NULL
                  AND f.asset_id = ANY(%s::uuid[])
                  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(%s) AS kept(id)
                                   WHERE kept.id::uuid = f.id)""",
            (PRODUCER, list(KINDS), [str(asset_id) for asset_id in scanned], db.jsonb(list(rows))),
        )


def standing(take_ids: Iterable[Any], kind: str | None = None) -> dict[Any, Spans[Mark]]:
    """Each take's flags as they stand, in the take's time, for the takes that have any.

    Flags sit on recordings; a passage is a stretch of a take. So each flag is
    carried over by where its recording was placed. One a person has dismissed
    no longer stands.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT ta.take_id, f.start_s + ta.offset_s, f.end_s + ta.offset_s, f.kind, f.action
             FROM media.take_assets ta
             JOIN media_video.flags f ON f.asset_id = ta.asset_id
            WHERE ta.take_id = ANY(%s::uuid[]) AND (ta.is_primary OR ta.offset_method IS NOT NULL)
              AND f.accepted IS DISTINCT FROM false AND (%s::text IS NULL OR f.kind = %s)""",
        ([str(take_id) for take_id in take_ids], kind, kind),
    )
    found: dict[Any, list[Mark]] = {}
    for take_id, start, end, flagged, action in cur.fetchall():
        found.setdefault(take_id, []).append(Mark(float(start), float(end), flagged, action))
    return {take_id: Spans(marks) for take_id, marks in found.items()}


def withheld(passage_ids: Iterable[Any]) -> set[str]:
    """Which of these passages hold something someone asked not to be used.

    Read from the flags as they stand, not from a score worked out earlier: a
    passage must not get through because a number was stale. A flag a person
    has dismissed no longer counts.
    """
    cur = db.cursor()
    cur.execute("SELECT id, take_id, start_s, end_s FROM media_video.passages WHERE id = ANY(%s::uuid[])",
                ([str(passage_id) for passage_id in passage_ids],))
    passages = cur.fetchall()
    asked = standing({take_id for _, take_id, _, _ in passages}, kind="off_record")
    return {str(passage_id) for passage_id, take_id, start, end in passages
            if take_id in asked and asked[take_id].within(float(start), float(end))}
