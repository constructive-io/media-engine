"""Keeping transcripts right: mending what is known to be misheard, and listening again where the first listen was weak.

Telling a recogniser a project's vocabulary helps while it is deciding. It
does not catch the plural it never saw, or the name it split in two, and a
handful of wrong words is no reason to listen to an hour again. So a term can
carry what it is misheard as, and mending rewrites exactly those: the
mishearing becomes the term, with a plural or a possessive kept.

Nothing is used up by it. A segment keeps what the recogniser wrote in
`text_asr`, a word keeps it in `word_asr`, and every change is a row in
`corrections` saying what the line was, what it became and which mishearing
did it. So a rule that turns out wrong is undone whole, with `restore`, and
everything a rule did can be read before it is trusted.

Segments and words are mended together. They are rows of their own, read by
different things: search reads the lines, cutting reads the words. A
transcript that disagrees with itself is worse than one that is wrong
throughout, because nothing shows it. One exception is kept on purpose: a
mishearing several words long is mended in the line and its words are left as
heard, since each of them has a time of its own and the term has only one.

Listening again is the other half. A quick recogniser is the right first pass
over everything, and some recordings defeat it: it is unsure of what it wrote,
or it wrote very little for the length of the recording. Those are listened to
again with another recogniser, and the new transcript replaces the old only
where it is better: surer of itself, and with no less found. A second listen
that is worse changes nothing, since it is compared before anything is
written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from ..settings import Settings
from ..transcription import Transcriber, clean, transcriber as default_transcriber
from . import transcripts

#: A recording whose recogniser was less sure than this, on average, is listened to again.
#: It is the mean log-probability of its words: nothing is surer than 0.
UNSURE = -0.55
#: And one with fewer segments a minute than this: a recogniser that skips
#: speech looks sure of the little it kept.
SPARSE = 2.0
#: A recording shorter than this, in seconds, is too short for either to mean much.
MIN_LENGTH_S = 60.0
#: Who is written beside a correction.
_ACTOR = "mend"


class UpkeepError(ValueError):
    """A mishearing that cannot be recorded as it was given."""


@dataclass(frozen=True)
class Rule:
    """One mishearing of one term. With a project, it holds only in that project's recordings."""

    heard: str
    term: str
    project: str | None = None

    @property
    def name(self) -> str:
        return f"{self.heard} -> {self.term}"

    @property
    def one_word(self) -> bool:
        return len(self.heard.split()) == 1

    def pattern(self) -> re.Pattern[str]:
        """The mishearing as a whole word or words, in either case, with a plural or possessive after it."""
        said = r"\s+".join(re.escape(part) for part in self.heard.split())
        return re.compile(rf"(?<!\w){said}(?P<tail>['’]s|s)?(?!\w)", re.IGNORECASE)

    def mend(self, text: str) -> str:
        return self.pattern().sub(lambda found: self.term + (found.group("tail") or ""), text)

    def feeds_itself(self) -> bool:
        """Whether the term holds its own mishearing, so that mending twice would mend the mended."""
        return self.mend(self.term) != self.term


@dataclass(frozen=True)
class Mended:
    """One line that was mended, or with nothing kept would be."""

    file: str
    start_s: float
    before: str
    after: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class Reheard:
    """One recording listened to again, and what came of it.

    `sure` is how sure the recogniser was, as `UNSURE` is measured; None from
    a recogniser that does not say.
    """

    file: str
    was_model: str | None
    was_sure: float | None
    was_segments: int
    sure: float | None
    segments: int
    better: bool


def mishear(term: str, heard: Iterable[str], project: str | None = None, kind: str | None = None) -> int:
    """Record what `term` is misheard as, adding the term if it is new. Returns how many were not known."""
    term = term.strip()
    if not term:
        raise UpkeepError("a mishearing is recorded against a term, and none was given")
    new = []
    for one in dict.fromkeys(" ".join(one.split()) for one in heard):
        if not one:
            continue
        if one == term:
            raise UpkeepError(f"{term!r} cannot be misheard as itself")
        if Rule(one, term).feeds_itself():
            raise UpkeepError(
                f"{one!r} is part of {term!r}: mending it would mend the mended, again each time")
        new.append(one)
    transcripts.learn([term], project, kind)
    cur = db.cursor()
    cur.execute(
        """SELECT id, confusions FROM media_video.vocabulary
            WHERE term = %s::text AND project IS NOT DISTINCT FROM %s::text""",
        (term, project),
    )
    ((term_id, known),) = cur.fetchall()
    known = list(known or [])
    have = {one.lower() for one in known}
    added = [one for one in new if one.lower() not in have]
    if added:
        cur.execute("UPDATE media_video.vocabulary SET confusions = %s::text[] WHERE id = %s",
                    (known + added, term_id))
    return len(added)


def known(project: str | None = None) -> dict[str, list[str]]:
    """What each term a recording in `project` is transcribed with is misheard as, for those that are."""
    return {term: heard for term, heard in _vocabulary(project) if heard}


def _vocabulary(project: str | None) -> list[tuple[str, list[str]]]:
    cur = db.cursor()
    cur.execute(
        """SELECT term, confusions FROM media_video.vocabulary
            WHERE project IS NULL OR project = %s::text
            ORDER BY project NULLS FIRST, term""",
        (project,),
    )
    return [(term, list(heard or [])) for term, heard in cur.fetchall()]


def rules() -> list[Rule]:
    """Every mishearing recorded, the longest first, so a longer one is not cut short by one inside it."""
    cur = db.cursor()
    cur.execute("SELECT term, confusions, project FROM media_video.vocabulary WHERE confusions <> '{}'")
    found = [Rule(" ".join(heard.split()), term, project)
             for term, confusions, project in cur.fetchall() for heard in confusions if heard.strip()]
    found = [rule for rule in found if not rule.feeds_itself()]
    return sorted(found, key=lambda rule: (-len(rule.heard), rule.heard.lower(), rule.term))


def mend(project: str | None = None, match: str | None = None, *, keep: bool = True,
         asset_id: Any = None) -> list[Mended]:
    """Mend every line that holds a recorded mishearing. Returns the lines, in order.

    With `keep` off nothing is written: the lines are what would change. Run
    twice, the second finds nothing to do.
    """
    every = rules()
    if not every:
        return []
    cur = db.cursor()
    cur.execute(
        """SELECT s.id, a.path, a.project, s.start_s, s.text
             FROM media_video.segments s JOIN media.assets a ON a.id = s.asset_id
            WHERE (%s::text IS NULL OR a.project = %s) AND (%s = '' OR a.path ~* %s)
              AND (%s::uuid IS NULL OR a.id = %s::uuid)
            ORDER BY a.path, s.idx""",
        (project, project, match or "", match or "", asset_id, asset_id),
    )
    patterns = {rule: rule.pattern() for rule in every}
    mended = []
    for segment_id, path, its_project, start, text in cur.fetchall():
        fired, now = [], text or ""
        for rule in every:
            if rule.project is not None and rule.project != its_project:
                continue
            if not patterns[rule].search(now):
                continue
            after = rule.mend(now)
            if after != now:
                fired.append((rule, now, after))
                now = after
        if not fired:
            continue
        mended.append(Mended(Path(path).name, float(start), text, now, tuple(rule.name for rule, _, _ in fired)))
        if keep:
            _keep(segment_id, now, fired)
    return mended


def _keep(segment_id: Any, text: str, fired: list[tuple[Rule, str, str]]) -> None:
    """Write one mended line: the line, its words, and what was done to it, together or not at all."""
    with db.transaction():
        cur = db.cursor()
        cur.executemany(
            """INSERT INTO media_video.corrections (segment_id, before_text, after_text, rule, actor)
               VALUES (%s, %s, %s, %s, %s)""",
            [(segment_id, before, after, rule.name, _ACTOR) for rule, before, after in fired],
        )
        cur.execute(
            "UPDATE media_video.segments SET text_asr = coalesce(text_asr, text), text = %s WHERE id = %s",
            (text, segment_id),
        )
        cur.execute("SELECT id, word FROM media_video.words WHERE segment_id = %s ORDER BY idx", (segment_id,))
        changed = []
        for word_id, word in cur.fetchall():
            now = word
            for rule, _, _ in fired:
                if rule.one_word:
                    now = rule.mend(now)
            if now != word:
                changed.append((now, word_id))
        if changed:
            cur.executemany(
                "UPDATE media_video.words SET word_asr = coalesce(word_asr, word), word = %s WHERE id = %s",
                changed,
            )


def restore(project: str | None = None, match: str | None = None) -> tuple[int, int]:
    """Put every line and word back to what the recogniser wrote. Returns (lines put back, corrections dropped)."""
    scope = """(%s::text IS NULL OR a.project = %s) AND (%s = '' OR a.path ~* %s)"""
    given = (project, project, match or "", match or "")
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            f"""UPDATE media_video.words w SET word = w.word_asr, word_asr = NULL
                  FROM media_video.segments s JOIN media.assets a ON a.id = s.asset_id
                 WHERE s.id = w.segment_id AND w.word_asr IS NOT NULL AND {scope}""",
            given,
        )
        cur.execute(
            f"""DELETE FROM media_video.corrections c
                 USING media_video.segments s JOIN media.assets a ON a.id = s.asset_id
                 WHERE s.id = c.segment_id AND {scope}
             RETURNING c.id""",
            given,
        )
        dropped = len(cur.fetchall())
        cur.execute(
            f"""UPDATE media_video.segments s SET text = s.text_asr
                  FROM media.assets a
                 WHERE a.id = s.asset_id AND s.text_asr IS NOT NULL AND s.text IS DISTINCT FROM s.text_asr
                   AND {scope}
             RETURNING s.id""",
            given,
        )
        restored = len(cur.fetchall())
    return restored, dropped


def weakest(project: str | None = None, match: str | None = None, *, model: str | None = None,
            unsure: float = UNSURE, sparse: float = SPARSE, min_length_s: float = MIN_LENGTH_S,
            limit: int | None = None) -> list[tuple[Any, str, str, str | None, str | None, float | None, int]]:
    """The recordings worth listening to again, the least sure first.

    Each is (asset id, path, the file to listen to, project, the model that
    wrote its transcript, how sure it was, segments). One whose transcript
    `model` already wrote is left out: the same recogniser would write the
    same thing.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path, coalesce(a.proxy_path, a.path), a.project,
                  min(s.model), avg(s.avg_logprob), count(*)
             FROM media_video.segments s JOIN media.assets a ON a.id = s.asset_id
            WHERE a.duration_s >= %s::float8
              AND (%s::text IS NULL OR a.project = %s) AND (%s = '' OR a.path ~* %s)
            GROUP BY a.id
           HAVING (%s::text IS NULL OR bool_or(s.model IS DISTINCT FROM %s::text))
              AND (avg(s.avg_logprob) < %s::float8 OR count(*) / nullif(a.duration_s / 60.0, 0) < %s::float8)
            ORDER BY avg(s.avg_logprob) NULLS LAST, a.path
            LIMIT %s""",
        (min_length_s, project, project, match or "", match or "", model, model, unsure, sparse, limit),
    )
    return [(asset_id, path, source, its_project, was, None if sure is None else float(sure), int(count))
            for asset_id, path, source, its_project, was, sure, count in cur.fetchall()]


def sureness(heard: Iterable[Any]) -> float | None:
    """How sure a recogniser was of a transcript: the mean over the segments it said so for."""
    said = [segment.avg_logprob for segment in heard if segment.avg_logprob is not None]
    return sum(said) / len(said) if said else None


def better(was_sure: float | None, was_segments: int, sure: float | None, segments: int) -> bool:
    """Whether a second transcript should replace the first.

    It has to be surer and to have found no less. Either alone is not enough:
    a recogniser that drops the hard parts is surer of what is left, and one
    that writes down the noise finds more. Where either recogniser does not
    say how sure it was there is only the count to go on, and it has to have
    found more.
    """
    if was_sure is None or sure is None:
        return segments > was_segments
    return sure > was_sure and segments >= was_segments


def relisten(
    project: str | None = None,
    match: str | None = None,
    *,
    language: str = "en",
    vocabulary: bool = True,
    unsure: float = UNSURE,
    sparse: float = SPARSE,
    min_length_s: float = MIN_LENGTH_S,
    limit: int | None = None,
    keep: bool = True,
    transcriber: Transcriber | None = None,
    settings: Settings | None = None,
) -> list[Reheard]:
    """Listen again to the weakest recordings, and keep each new transcript only where it is better.

    A transcript that is replaced takes with it what was drawn from the old
    one, as `transcribe --force` does, and what was mended in the old one is
    mended in the new. With `keep` off the listening is done and nothing is
    written.
    """
    if transcriber is None:
        transcriber = default_transcriber(settings=settings)
    done = []
    for asset_id, path, source, its_project, was_model, was_sure, was_segments in weakest(
            project, match, model=transcriber.model, unsure=unsure, sparse=sparse,
            min_length_s=min_length_s, limit=limit):
        prompt = ", ".join(transcripts.terms(its_project)) if vocabulary else ""
        heard = clean(transcriber.transcribe(Path(source), language=language, prompt=prompt or None),
                      prompt or None)
        sure = sureness(heard)
        wins = better(was_sure, was_segments, sure, len(heard))
        if wins and keep:
            transcripts.rewrite(asset_id, heard, transcriber.model)
            mend(asset_id=asset_id)
        done.append(Reheard(Path(path).name, was_model, was_sure, was_segments, sure, len(heard), wins))
    return done
