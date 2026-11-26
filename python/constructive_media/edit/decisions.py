"""Decisions: what a person ruled.

A score is a measurement and a decision is a ruling, and the two are kept
apart so that neither can pass for the other. Three kinds of ruling are
stored:

- keep or cut, about one passage or shot. A cut is also an exclusion, which
  every ranking and every later cut honours. Keeping something again lifts it.
- a remark that is neither: trim this, not sure about that. It changes nothing
  by itself and is there for whoever cuts next.
- a preference, this one over that one. Nobody can say what makes a shot good
  as a number, and anybody can say which of two they would rather use, so
  preferences are what a ranker is later taught from.

Saying the same thing twice is recorded once. Changing your mind is recorded
as a new ruling, and the latest one stands.
"""

from __future__ import annotations

import getpass
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import EditError, clock

SOURCES = ("human", "model", "baseline")
#: The fewest characters of an id that will be looked up.
SHORTEST = 6

_HEX = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True)
class Subject:
    """The passage or shot a ruling is about."""

    kind: str
    id: Any
    #: Where it is, for a person: the take or file, and the time.
    where: str = ""

    def __str__(self) -> str:
        return f"{self.kind} {self.short}" + (f" ({self.where})" if self.where else "")

    @property
    def short(self) -> str:
        """As much of the id as is shown, and enough to give back."""
        return str(self.id).replace("-", "")[:12]


@dataclass(frozen=True)
class Pair:
    """Two things worth being asked to choose between."""

    one: Subject
    other: Subject
    #: How far apart the recipe scored them.
    gap: float
    #: How alike they look, 0 to 1, when that is known.
    likeness: float | None = None


@dataclass(frozen=True)
class Ruling:
    when: Any
    actor: str
    source: str
    kind: str
    keep: bool | None
    subject: str
    over: str | None
    note: str | None


def me() -> str:
    """Who is deciding, when nobody says: whoever is logged in."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def find(prefix: Any, kind: str | None = None) -> Subject:
    """The passage or shot whose id starts with `prefix`.

    A prefix that fits two things, or none, is an error: a ruling recorded
    against a guess is worse than no ruling.
    """
    if isinstance(prefix, Subject):
        return prefix
    bare = str(prefix).strip().lower().replace("-", "")
    if not SHORTEST <= len(bare) <= 32 or not _HEX.fullmatch(bare):
        raise EditError(f"{prefix!r} is not an id: give at least the first {SHORTEST} characters of one")
    cur = db.cursor()
    cur.execute(
        """SELECT 'passage', p.id, t.name, p.start_s, p.end_s
             FROM media_video.passages p JOIN media.takes t ON t.id = p.take_id
            WHERE %s IN ('any', 'passage') AND replace(p.id::text, '-', '') LIKE %s
           UNION ALL
           SELECT 'shot', sh.id, a.path, sh.start_s, sh.end_s
             FROM media_video.shots sh JOIN media.assets a ON a.id = sh.asset_id
            WHERE %s IN ('any', 'shot') AND replace(sh.id::text, '-', '') LIKE %s
            LIMIT 3""",
        (kind or "any", bare + "%", kind or "any", bare + "%"),
    )
    found = cur.fetchall()
    what = f"{kind}'s" if kind else "passage's or shot's"
    if not found:
        raise EditError(f"no {what} id starts with {prefix!r}")
    if len(found) > 1:
        raise EditError(f"more than one {what} id starts with {prefix!r}; give more of it")
    ((found_kind, found_id, source, start, end),) = found
    return Subject(found_kind, found_id, _where(found_kind, source, start, end))


def _where(kind: str, source: str, start: float, end: float) -> str:
    return f"{source if kind == 'passage' else Path(source).name} {clock(float(start))}-{clock(float(end))}"


def _record(
    kind: str, subject: Subject, *, keep: bool | None = None, over: Subject | None = None,
    note: str | None = None, actor: str | None = None, source: str = "human",
) -> bool:
    """Store one ruling, unless it is what this person last said about the same thing."""
    if source not in SOURCES:
        raise EditError(f"a decision is {', '.join(SOURCES[:-1])} or {SOURCES[-1]}, not {source!r}")
    actor = actor or me()
    column = "chosen_shot" if subject.kind == "shot" else "passage_id"
    against = "rejected_shot" if subject.kind == "shot" else "rejected_passage"
    cur = db.cursor()
    if over is None:
        cur.execute(
            f"""SELECT keep, context FROM media_video.decisions
                 WHERE kind = %s AND {column} = %s AND actor = %s AND source_of = %s
                 ORDER BY decided_at DESC, id DESC LIMIT 1""",
            (kind, subject.id, actor, source),
        )
        if (last := cur.fetchone()) and (last[0], last[1]) == (keep, note):
            return False
    else:
        cur.execute(
            f"""SELECT {column}, context FROM media_video.decisions
                 WHERE kind = %s AND actor = %s AND source_of = %s
                   AND ({column}, {against}) IN ((%s::uuid, %s::uuid), (%s::uuid, %s::uuid))
                 ORDER BY decided_at DESC, id DESC LIMIT 1""",
            (kind, actor, source, str(subject.id), str(over.id), str(over.id), str(subject.id)),
        )
        if (last := cur.fetchone()) and (str(last[0]), last[1]) == (str(subject.id), note):
            return False
    # The clock, not the transaction's start: two rulings made together still have an order.
    cur.execute(
        f"""INSERT INTO media_video.decisions (kind, {column}, {against}, keep, context, actor, source_of, decided_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, clock_timestamp())""",
        (kind, subject.id, over.id if over else None, keep, note, actor, source),
    )
    return True


def _scope(episode: str | None) -> Any:
    if episode is None:
        return None
    from .timelines import episode_id

    return episode_id(episode)


def keep(
    subject: Any, *, note: str | None = None, episode: str | None = None, actor: str | None = None,
    source: str = "human",
) -> bool:
    """Rule that a passage or shot is to be used. Lifts a cut made in the same scope.

    Returns whether anything changed: False when this is what was last said.
    """
    subject = find(subject)
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            f"""DELETE FROM media_video.exclusions
                 WHERE {subject.kind}_id = %s AND episode_id IS NOT DISTINCT FROM %s RETURNING id""",
            (subject.id, _scope(episode)),
        )
        lifted = bool(cur.fetchall())
        recorded = _record(f"{subject.kind}_keep", subject, keep=True, note=note, actor=actor, source=source)
    return recorded or lifted


def cut(
    subject: Any, *, note: str | None = None, episode: str | None = None, actor: str | None = None,
    source: str = "human",
) -> bool:
    """Rule a passage or shot out: of everything, or of one episode when it is named."""
    subject = find(subject)
    actor = actor or me()
    with db.transaction():
        scope = _scope(episode)
        cur = db.cursor()
        cur.execute(
            f"""INSERT INTO media_video.exclusions ({subject.kind}_id, episode_id, reason, actor)
                SELECT %s, %s, %s, %s
                 WHERE NOT EXISTS (SELECT 1 FROM media_video.exclusions
                                    WHERE {subject.kind}_id = %s AND episode_id IS NOT DISTINCT FROM %s)
                RETURNING id""",
            (subject.id, scope, note, actor, subject.id, scope),
        )
        excluded = bool(cur.fetchall())
        recorded = _record(f"{subject.kind}_keep", subject, keep=False, note=note, actor=actor, source=source)
    return recorded or excluded


def remark(subject: Any, mark: str, note: str | None = None, *, actor: str | None = None) -> bool:
    """Say something about a passage or shot that is neither keep nor cut, such as `trim`."""
    subject = find(subject)
    said = f"[{mark}]" + (f" {note}" if note else "")
    return _record(f"{subject.kind}_keep", subject, keep=None, note=said, actor=actor)


def prefer(
    chosen: Any, rejected: Any, *, why: str | None = None, actor: str | None = None, source: str = "human",
) -> bool:
    """Rule that `chosen` is the better of two passages or two shots."""
    chosen, rejected = find(chosen), find(rejected)
    if chosen.kind != rejected.kind:
        raise EditError(f"a {chosen.kind} and a {rejected.kind} cannot be compared: choose between two of a kind")
    if str(chosen.id) == str(rejected.id):
        raise EditError("that is the same thing twice; a preference is between two")
    return _record(f"{chosen.kind}_pref", chosen, over=rejected, note=why, actor=actor, source=source)


def standing(subject_ids: Iterable[Any]) -> dict[str, tuple[bool | None, str | None]]:
    """The last thing a person ruled about each passage: (keep, note), by passage id."""
    cur = db.cursor()
    cur.execute(
        """SELECT DISTINCT ON (d.passage_id) d.passage_id, d.keep, d.context
             FROM media_video.decisions d
            WHERE d.kind = 'passage_keep' AND d.source_of = 'human' AND d.passage_id = ANY(%s::uuid[])
            ORDER BY d.passage_id, d.decided_at DESC, d.id DESC""",
        ([str(subject_id) for subject_id in subject_ids],),
    )
    return {str(passage_id): (kept, note) for passage_id, kept, note in cur.fetchall()}


def pairs(
    recipe: str = "broll", *, mode: str = "close", count: int = 10, project: str | None = None,
    look_ahead: int = 8, actor: str | None = None,
) -> list[Pair]:
    """Pairs most worth a person's judgement, by a recipe's ranking.

    `close` pairs things the recipe scores nearly alike: where it cannot tell
    them apart is where being told teaches it most. `contrast` pairs shots it
    scores alike that look unalike, which is where a number hides a real
    difference. Nothing appears twice, and nothing this person has already
    chosen between is asked again.
    """
    from . import scores, select

    if mode not in ("close", "contrast"):
        raise EditError(f"pairs are close or contrast, not {mode!r}")
    ranking = scores.ranked(recipe, limit=None, project=project)
    subject = ranking.recipe.subject
    if mode == "contrast" and subject != "shot":
        raise EditError(f"contrast compares how shots look, and {recipe!r} ranks {subject}s")
    entries = ranking.entries
    looks = select.signatures(entry.subject_id for entry in entries) if subject == "shot" else {}
    if mode == "contrast":
        entries = [entry for entry in entries if str(entry.subject_id) in looks]

    column, against = ("chosen_shot", "rejected_shot") if subject == "shot" else ("passage_id", "rejected_passage")
    cur = db.cursor()
    cur.execute(
        f"""SELECT {column}, {against} FROM media_video.decisions
             WHERE kind = %s AND actor = %s AND {column} IS NOT NULL AND {against} IS NOT NULL""",
        (f"{subject}_pref", actor or me()),
    )
    judged = {frozenset((str(one), str(other))) for one, other in cur.fetchall()}

    found: list[Pair] = []
    used: set[int] = set()
    for index, entry in enumerate(entries):
        if index in used:
            continue
        best: tuple[float, int, float | None] | None = None
        for ahead in range(index + 1, min(index + 1 + look_ahead, len(entries))):
            other = entries[ahead]
            if ahead in used or frozenset((str(entry.subject_id), str(other.subject_id))) in judged:
                continue
            one_look, other_look = looks.get(str(entry.subject_id)), looks.get(str(other.subject_id))
            likeness = select.similarity(one_look, other_look) if one_look and other_look else None
            # Closest in score, or least alike among the near in score.
            order = entry.score - other.score if mode == "close" else likeness
            if best is None or order < best[0]:
                best = (order, ahead, likeness)
        if best is None:
            continue
        _, ahead, likeness = best
        used.update((index, ahead))
        other = entries[ahead]
        found.append(Pair(
            Subject(subject, entry.subject_id, _where(subject, entry.source, entry.start_s, entry.end_s)),
            Subject(subject, other.subject_id, _where(subject, other.source, other.start_s, other.end_s)),
            entry.score - other.score, likeness,
        ))
    # Rounded, so that pairs as good as each other stay in the ranking's order.
    found.sort(key=lambda pair: round(pair.gap if mode == "close" else pair.likeness, 6))
    return found[:count]


def tally() -> dict[tuple[str, str], int]:
    """How many rulings there are, by (kind, who it came from)."""
    cur = db.cursor()
    cur.execute("SELECT kind, source_of, count(*) FROM media_video.decisions GROUP BY 1, 2 ORDER BY 1, 2")
    return {(kind, source): count for kind, source, count in cur.fetchall()}


def recent(limit: int = 20) -> list[Ruling]:
    """The latest rulings, newest first."""
    cur = db.cursor()
    cur.execute(
        """SELECT decided_at, actor, source_of, kind, keep,
                  coalesce(chosen_shot, passage_id), coalesce(rejected_shot, rejected_passage), context
             FROM media_video.decisions
            WHERE coalesce(chosen_shot, passage_id) IS NOT NULL
            ORDER BY decided_at DESC, id DESC LIMIT %s""",
        (limit,),
    )
    return [Ruling(when, actor, source, kind, kept, str(subject), str(over) if over else None, note)
            for when, actor, source, kind, kept, subject, over, note in cur.fetchall()]
