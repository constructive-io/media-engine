"""Passages: stretches of a take's speech that stand on their own.

A passage is a candidate, nothing more: a span of a take that starts and ends
where the talking does, long enough to say something and short enough to use.
Two ways of finding them:

- by silence    people stop between thoughts. Speech is cut where the room
                goes quiet, and anything too long is cut again at its longest
                pause. These are the exchanges: a question and its answer.
- by sentence   one sentence, from one voice. These are the lines to quote.

A passage is known by its take, its span and how it was found, so proposing
again finds the same ones instead of piling up copies. What a new run no
longer finds is removed, unless someone has passed judgement on it.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .. import db
from ..takes import Take
from . import EditError, said
from .quality import FAR, Quality, confidence, judge
from .said import Line, Spans

NAMESPACE = uuid.UUID("6f1c2c4e-8a3d-5b7f-9e21-4d6a8c0b1e35")
#: The most text one passage keeps.
MOST_TEXT = 8000


@dataclass(frozen=True)
class Passage:
    """A span of a take, in take time, and what is said in it."""

    start: float
    end: float
    text: str
    n_words: int
    #: Times the speaker changes.
    n_turns: int
    n_speakers: int
    question_n: int
    filler_n: int
    #: The longest quiet inside it.
    longest_gap_s: float
    #: Seconds in which two people talk at once.
    overlap_s: float
    quality: Quality


@dataclass(frozen=True)
class Proposed:
    """What proposing did to one take."""

    take: str
    method: str
    passages: int
    added: int
    removed: int
    #: Passages no longer found but kept, because someone judged them.
    held: int


def passage_id(take_id: Any, start_s: float, end_s: float, method: str) -> str:
    """The id of the passage at this span: the same span is the same passage."""
    return str(uuid.uuid5(NAMESPACE, f"{take_id}|{float(start_s):.2f}|{float(end_s):.2f}|{method}"))


def groups(
    told: Sequence[Line], *, min_gap_s: float = 1.2, min_len_s: float = 12.0, apart_s: float = 8.0,
) -> list[list[Line]]:
    """Cut lines into groups where the talking stops.

    A quiet of `min_gap_s` ends a group once it is `min_len_s` long; until
    then the group carries on across the pause, because a short remark and
    the reply to it belong together. A quiet of `apart_s` ends it regardless:
    whatever comes after that is another moment.
    """
    found: list[list[Line]] = []
    group: list[Line] = []
    reach = 0.0
    for line in told:
        if group:
            quiet = line.start - reach
            if quiet >= apart_s or (quiet >= min_gap_s and reach - group[0].start >= min_len_s):
                found.append(group)
                group = []
        reach = max(reach, line.end) if group else line.end
        group.append(line)
    if group:
        found.append(group)
    return found


def split(group: list[Line], max_len_s: float = 90.0) -> list[list[Line]]:
    """Cut a group that runs too long at its longest pause, and again if need be.

    Never within two lines of either end, which would shave off a fragment,
    so a group of fewer than four lines is left as it is.
    """
    if len(group) < 4 or max(line.end for line in group) - group[0].start <= max_len_s:
        return [group]
    reach, pauses = group[0].end, []
    for at in range(1, len(group)):
        pauses.append((group[at].start - reach, at))
        reach = max(reach, group[at].end)
    _, cut = max(pauses[1:-1], key=lambda pause: (pause[0], -pause[1]))
    return split(group[:cut], max_len_s) + split(group[cut:], max_len_s)


def sentences(heard: Sequence[Line], *, max_len_s: float = 12.0) -> list[Line]:
    """One recording's words, cut into sentences.

    A sentence ends on a full stop, a question mark or an exclamation mark, or
    when it has run for `max_len_s` without one.
    """
    found: list[Line] = []
    words: list[said.Heard] = []
    first: Line | None = None

    def close() -> None:
        if first is not None and words:
            found.append(Line(words[0][0], words[-1][1], " ".join(word.strip() for _, _, word in words),
                              first.who, first.asset_id, first.segment_id, first.logprob, first.no_speech))
        words.clear()

    for line in heard:
        for word in line.words:
            if words and word[1] - words[0][0] > max_len_s:
                close()
            if not words:
                first = line
            words.append(word)
            if word[2].rstrip("\"')”’ ").endswith((".", "!", "?")):
                close()
    close()
    return found


def measure(
    piece: Sequence[Line], heard: Spans[Line], *, before: float | None = None, after: float | None = None,
) -> Passage:
    """Describe the passage these lines make.

    `piece` is what is said in it, once; `heard` is every line of the take, to
    judge the recogniser by; `before` and `after` are where the speech on
    either side of it ends and begins.
    """
    start, end = round(piece[0].start, 2), round(max(line.end for line in piece), 2)
    text = " ".join(line.text.strip() for line in piece if line.text.strip())[:MOST_TEXT]
    words = said.tokens(text)
    reach, quiet = piece[0].end, 0.0
    for line in piece[1:]:
        quiet = max(quiet, line.start - reach)
        reach = max(reach, line.end)
    return Passage(
        start, end, text,
        n_words=len(words),
        n_turns=sum(1 for one, other in zip(piece, piece[1:]) if one.who != other.who),
        n_speakers=len({line.who for line in piece}),
        question_n=text.count("?"),
        filler_n=said.fillers(words),
        longest_gap_s=round(quiet, 2),
        overlap_s=round(min(said.talked_over(piece), end - start), 2),
        quality=judge(
            text, confidence=confidence(heard.within(start, end), start, end),
            head_gap_s=FAR if before is None else max(0.0, start - before),
            tail_gap_s=FAR if after is None else max(0.0, after - end),
        ),
    )


def by_silence(
    heard: Sequence[Line], *, min_gap_s: float = 1.2, min_len_s: float = 12.0, max_len_s: float = 90.0,
    apart_s: float = 8.0,
) -> list[Passage]:
    """The passages a take's lines fall into where the talking stops."""
    told = said.once(heard)
    pieces = [piece
              for group in groups(told, min_gap_s=min_gap_s, min_len_s=min_len_s, apart_s=apart_s)
              for piece in split(group, max_len_s)]
    every = Spans(heard)
    return [found for piece, before, after in _between(pieces, told)
            if (found := measure(piece, every, before=before, after=after)).end - found.start >= min_len_s]


def by_sentence(
    heard: Sequence[Line], *, min_len_s: float = 1.2, max_len_s: float = 12.0, min_words: int = 4,
) -> list[Passage]:
    """Each sentence said in a take, once, however many microphones heard it."""
    recordings: dict[Any, list[Line]] = {}
    for line in heard:
        recordings.setdefault(line.asset_id, []).append(line)
    told = said.once(sentence for lines in recordings.values()
                     for sentence in sentences(lines, max_len_s=max_len_s))
    every = Spans(heard)
    return [found for piece, before, after in _between([[line] for line in told], told)
            if (found := measure(piece, every, before=before, after=after)).end - found.start >= min_len_s
            and found.n_words >= min_words]


def _between(pieces: Sequence[Sequence[Line]], told: Sequence[Line]) -> list[tuple[Sequence[Line], Any, Any]]:
    """Each piece with where the speech before it ends and the speech after it begins.

    The pieces are `told` cut up in order, nothing left out.
    """
    found, at, reach = [], 0, None
    for piece in pieces:
        after = told[at + len(piece)].start if at + len(piece) < len(told) else None
        found.append((piece, reach, after))
        reach = max([line.end for line in piece] + ([] if reach is None else [reach]))
        at += len(piece)
    return found


#: How passages are found: the name each way's passages are stored under, and
#: what finds them.
WAYS = {"silence": ("gap", by_silence), "sentence": ("sentence", by_sentence)}


def propose(take: Take, *, by: str = "silence", name: str | None = None, **options: float) -> Proposed:
    """Find one take's passages and store them.

    `options` are the finder's own: see `by_silence` and `by_sentence`. `name`
    stores the passages apart from the usual ones found the same way, so two
    settings can be compared side by side.
    """
    if by not in WAYS:
        raise EditError(f"passages are found by {' or '.join(WAYS)}, not by {by!r}")
    method, find = WAYS[by]
    if unknown := set(options) - set(inspect.signature(find).parameters):
        raise EditError(f"finding passages by {by} has no {', '.join(sorted(unknown))} to set")
    return _store(take, name or method, find(said.lines(take.id, with_words=by == "sentence"), **options))


def _store(take: Take, method: str, found: Sequence[Passage]) -> Proposed:
    rows = {}
    for passage in found:
        fields = asdict(passage)
        fields.update(fields.pop("quality"), start_s=fields.pop("start"), end_s=fields.pop("end"))
        fields["id"] = passage_id(take.id, passage.start, passage.end, method)
        rows[fields["id"]] = fields
    with db.transaction():
        cur = db.cursor()
        cur.execute("SELECT id FROM media_video.passages WHERE take_id = %s AND method = %s", (take.id, method))
        before = {str(row[0]) for row in cur.fetchall()}
        cur.execute(
            """WITH found AS (
                 SELECT * FROM jsonb_to_recordset(%s) AS p(
                   id uuid, start_s double precision, end_s double precision, text text,
                   n_words integer, n_turns integer, n_speakers integer, question_n integer,
                   filler_n integer, longest_gap_s double precision, overlap_s double precision,
                   ends_complete boolean, starts_complete boolean, tail_gap_s double precision,
                   head_gap_s double precision, repetition double precision,
                   fragment_ratio double precision, lexical_variety double precision,
                   mean_sentence_w double precision, coherence double precision)
               ), written AS (
                 INSERT INTO media_video.passages
                        (id, take_id, start_s, end_s, method, text, n_words, n_turns, n_speakers,
                         question_n, filler_n, longest_gap_s, overlap_s)
                 SELECT id, %s, start_s, end_s, %s, text, n_words, n_turns, n_speakers,
                        question_n, filler_n, longest_gap_s, overlap_s
                   FROM found
                 ON CONFLICT (id) DO UPDATE SET
                   text = EXCLUDED.text, n_words = EXCLUDED.n_words, n_turns = EXCLUDED.n_turns,
                   n_speakers = EXCLUDED.n_speakers, question_n = EXCLUDED.question_n,
                   filler_n = EXCLUDED.filler_n, longest_gap_s = EXCLUDED.longest_gap_s,
                   overlap_s = EXCLUDED.overlap_s
               )
               INSERT INTO media_video.passage_quality
                      (passage_id, ends_complete, starts_complete, tail_gap_s, head_gap_s, repetition,
                       fragment_ratio, lexical_variety, mean_sentence_w, coherence)
               SELECT id, ends_complete, starts_complete, tail_gap_s, head_gap_s, repetition,
                      fragment_ratio, lexical_variety, mean_sentence_w, coherence
                 FROM found
               ON CONFLICT (passage_id) DO UPDATE SET
                 ends_complete = EXCLUDED.ends_complete, starts_complete = EXCLUDED.starts_complete,
                 tail_gap_s = EXCLUDED.tail_gap_s, head_gap_s = EXCLUDED.head_gap_s,
                 repetition = EXCLUDED.repetition, fragment_ratio = EXCLUDED.fragment_ratio,
                 lexical_variety = EXCLUDED.lexical_variety, mean_sentence_w = EXCLUDED.mean_sentence_w,
                 coherence = EXCLUDED.coherence, computed_at = now()""",
            (db.jsonb(list(rows.values())), take.id, method),
        )
        gone = sorted(before - set(rows))
        cur.execute(
            """DELETE FROM media_video.passages p
                WHERE p.id = ANY(%s::uuid[])
                  AND NOT EXISTS (SELECT 1 FROM media_video.decisions d
                                   WHERE p.id IN (d.passage_id, d.rejected_passage))
                  AND NOT EXISTS (SELECT 1 FROM media_video.exclusions x WHERE x.passage_id = p.id)
            RETURNING p.id""",
            (gone,),
        )
        removed = len(cur.fetchall())
    return Proposed(take.name, method, len(rows), len(set(rows) - before), removed, len(gone) - removed)
