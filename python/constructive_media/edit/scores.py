"""Scores: what is known about each passage and shot, and rankings made from it.

Every measurement the engine has of a thing, whether the sentence finished,
how sure the recogniser was, how sharp the picture is, is written down as a
score from 0 to 1 with where it came from. A recipe is a set of weights over
those scores, and a ranking is a recipe applied to whatever has been measured
so far. Three rules hold it together:

- A score that is missing is not a zero. A passage nobody has looked at the
  picture of is unjudged, not bad, so the weights are shared out over the
  scores that exist and every ranking says how much of its recipe it had to
  go on.
- Several senses have to agree. What was said, how it sounded and how it
  looked are each a weak claim alone, and a recipe can ask for more than one
  before a thing may rank at all.
- Some things are not a matter of degree. A passage in which someone asks for
  it not to be used is left out of every ranking, whatever it scores.

Scores are redone, never piled up: scoring twice leaves what scoring once did.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import db
from . import EditError, flags, said
from .quality import recognised

#: What a score cost to work out: known from the file itself, read from the
#: transcript, taken from analysed frames or sound, or a model's opinion. A
#: ranking reports the deepest it drew on.
FREE, READ, SEEN, JUDGED = 0, 1, 2, 3

#: Each score: (subject, id, name, value, raw, cost, sense, where it came from).
Score = tuple[str, Any, str, float, float | None, int, str, str]

_WRITE = """
INSERT INTO media_video.scores (subject_type, subject_id, name, value, raw, tier, modality, producer)
SELECT subject_type, subject_id, name, greatest(0, least(1, value)), raw, tier, modality, producer
  FROM ({rows}) AS found(subject_type, subject_id, name, value, raw, tier, modality, producer)
 WHERE value IS NOT NULL
ON CONFLICT (subject_type, subject_id, name, producer) DO UPDATE SET
  value = EXCLUDED.value, raw = EXCLUDED.raw, tier = EXCLUDED.tier,
  modality = EXCLUDED.modality, computed_at = now()
RETURNING name"""

#: A take's placed recordings.
_PLACED = "(ta.is_primary OR ta.offset_method IS NOT NULL)"
#: A cut that applies: one made for good, or one made for the episode asked about.
_CUT = "(x.episode_id IS NULL OR x.episode_id = (SELECT id FROM media.episodes WHERE slug = %s))"

_TEXT = f"""
SELECT 'passage', p.id, s.name, s.value, s.raw, {READ}, s.modality, s.producer
  FROM media_video.passages p
  JOIN media.takes t ON t.id = p.take_id
  LEFT JOIN media_video.passage_quality q ON q.passage_id = p.id
 CROSS JOIN LATERAL (SELECT greatest(p.end_s - p.start_s, 0.01) AS seconds) d
 CROSS JOIN LATERAL (VALUES
   ('coherence', q.coherence, NULL, 'text', 'passage_quality'),
   -- How it ends counts for more: opening on "So" is how people talk, and
   -- stopping in the middle of a sentence cannot be used.
   ('completeness', 0.75 * q.ends_complete::int + 0.25 * q.starts_complete::int, NULL,
    'text', 'passage_quality'),
   -- By the minute, not the count: a long passage gathers turns and
   -- questions without being any more of a conversation.
   ('exchange', p.n_turns / d.seconds * 20, p.n_turns, 'text', 'passages'),
   ('question_density', p.question_n / d.seconds * 30, p.question_n, 'text', 'passages'),
   -- Dead air and people talking over each other are heard, not read, though
   -- they are worked out from when the words fall.
   ('speech_clarity',
    1 - least(1, p.longest_gap_s / 3) - least(0.4, p.overlap_s / d.seconds), NULL, 'audio', 'passages')
 ) AS s(name, value, raw, modality, producer)
 WHERE %s::text IS NULL OR t.project = %s"""

#: Each passage as each of its take's cameras saw it. Sharpness is measured
#: against the sharpest footage of the same project, because the figure has no
#: scale of its own: it depends on the lens, the light and the frame size.
_SEEN = f"""
WITH sharpest AS (
  SELECT a.project, percentile_cont(0.95) WITHIN GROUP (ORDER BY sh.sharpness) AS sharpness
    FROM media_video.shots sh
    JOIN media.assets a ON a.id = sh.asset_id
   WHERE sh.sharpness IS NOT NULL AND NOT coalesce(sh.is_black, false)
   GROUP BY a.project
), seen AS (
  SELECT p.id,
         sum(sh.sharpness * o.shared) FILTER (WHERE sh.sharpness IS NOT NULL)
           / nullif(sum(o.shared) FILTER (WHERE sh.sharpness IS NOT NULL), 0) AS sharpness,
         coalesce(sum(o.shared) FILTER (WHERE sh.subject_n > 0), 0)
           / nullif(sum(o.shared) FILTER (WHERE sh.subject_n IS NOT NULL), 0) AS present,
         max(top.sharpness) AS sharpest
    FROM media_video.passages p
    JOIN media.takes t ON t.id = p.take_id
    JOIN media.take_assets ta ON ta.take_id = p.take_id AND {_PLACED}
    JOIN media.assets a ON a.id = ta.asset_id
    JOIN media_video.shots sh ON sh.asset_id = ta.asset_id AND NOT coalesce(sh.is_black, false)
     AND sh.start_s + ta.offset_s < p.end_s AND sh.end_s + ta.offset_s > p.start_s
   CROSS JOIN LATERAL (SELECT least(sh.end_s + ta.offset_s, p.end_s)
                            - greatest(sh.start_s + ta.offset_s, p.start_s) AS shared) o
    LEFT JOIN sharpest top ON top.project IS NOT DISTINCT FROM a.project
   WHERE %s::text IS NULL OR t.project = %s
   GROUP BY p.id, ta.asset_id
)
-- The best camera speaks for the passage: it is the one the cut will use.
SELECT 'passage', id, 'picture_quality', max(sharpness / nullif(sharpest, 0)), max(sharpness),
       {SEEN}, 'picture', 'frames'
  FROM seen GROUP BY id
UNION ALL
SELECT 'passage', id, 'subject_present', max(present), NULL, {SEEN}, 'picture', 'detections'
  FROM seen GROUP BY id"""

_SHOTS = f"""
WITH sharpest AS (
  SELECT a.project, percentile_cont(0.95) WITHIN GROUP (ORDER BY sh.sharpness) AS sharpness
    FROM media_video.shots sh
    JOIN media.assets a ON a.id = sh.asset_id
   WHERE sh.sharpness IS NOT NULL AND NOT coalesce(sh.is_black, false)
   GROUP BY a.project
)
SELECT 'shot', sh.id, s.name, s.value, s.raw, {SEEN}, 'picture', s.producer
  FROM media_video.shots sh
  JOIN media.assets a ON a.id = sh.asset_id
  LEFT JOIN sharpest top ON top.project IS NOT DISTINCT FROM a.project
  LEFT JOIN media_video.signatures sig ON sig.shot_id = sh.id
  LEFT JOIN media_video.v_shot_content c ON c.shot_id = sh.id
 CROSS JOIN LATERAL (VALUES
   ('sharpness', sh.sharpness / nullif(top.sharpness, 0), sh.sharpness, 'frames'),
   ('colour_variety', sig.sat_mean * 2, sig.sat_mean, 'signatures'),
   -- Only where a detector has looked. Before that, nobody in the frame and
   -- nobody having checked are the same empty column, and only one of them
   -- makes a shot safe to lay over someone else's voice.
   ('subject_present', (sh.subject_n > 0)::int, sh.subject_n, 'detections'),
   ('safe_over_audio', CASE WHEN sh.subject_n IS NOT NULL THEN c.safe_over_other_audio::int END,
    NULL, 'detections')
 ) AS s(name, value, raw, producer)
 WHERE NOT coalesce(sh.is_black, false) AND (%s::text IS NULL OR a.project = %s)"""

@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    #: What it ranks: `passage` or `shot`.
    subject: str
    weights: dict[str, float]
    #: How many senses must have a score before a thing ranks.
    min_modalities: int


@dataclass(frozen=True)
class Entry:
    """One thing in a ranking."""

    subject_id: Any
    score: float
    #: Share of the recipe's weight that had a score to go on, 0 to 1.
    coverage: float
    modalities: int
    deepest: int
    contributing: tuple[str, ...]
    #: The take a passage is from, or the file a shot is in.
    source: str
    start_s: float
    end_s: float
    text: str = ""


@dataclass(frozen=True)
class Ranking:
    recipe: Recipe
    entries: list[Entry]
    #: Passages that would have ranked but hold something someone asked not
    #: to be used.
    withheld: int = 0


def record(scores: Iterable[Score]) -> Counter[str]:
    """Write scores, replacing any the same producer gave the same thing before."""
    names = ("subject_type", "subject_id", "name", "value", "raw", "tier", "modality", "producer")
    return _write(
        """SELECT * FROM jsonb_to_recordset(%s) AS r(
             subject_type text, subject_id uuid, name text, value double precision,
             raw double precision, tier smallint, modality text, producer text)""",
        (db.jsonb([dict(zip(names, (subject, str(subject_id), *rest), strict=True))
                   for subject, subject_id, *rest in scores]),),
    )


def _write(rows: str, params: Sequence[Any]) -> Counter[str]:
    cur = db.cursor()
    cur.execute(_WRITE.format(rows=rows), tuple(params))
    return Counter(row[0] for row in cur.fetchall())


def _passages(project: str | None) -> dict[Any, list[tuple[Any, float, float]]]:
    """Every passage, or one project's, under its take: `(id, start, end)`."""
    cur = db.cursor()
    cur.execute(
        """SELECT p.take_id, p.id, p.start_s, p.end_s
             FROM media_video.passages p JOIN media.takes t ON t.id = p.take_id
            WHERE %s::text IS NULL OR t.project = %s""", (project, project))
    found: dict[Any, list[tuple[Any, float, float]]] = {}
    for take_id, passage_id, start, end in cur.fetchall():
        found.setdefault(take_id, []).append((passage_id, float(start), float(end)))
    return found


def recogniser(project: str | None = None) -> Counter[str]:
    """How sure the recogniser was of each passage, on the recording that heard it best."""
    found: list[Score] = []
    for take_id, passages in _passages(project).items():
        heard = said.Spans(said.lines(take_id))
        for passage_id, start, end in passages:
            if (sure := recognised(heard.within(start, end), start, end)) is not None:
                # It is about the sound, though a transcript is where it is
                # read from: muffled speech is what a recogniser is unsure of.
                found.append(("passage", passage_id, "asr_confidence", sure[0], sure[1],
                              READ, "audio", "transcript"))
    return record(found)


def covered(spans: Iterable[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of a stretch that spans cover, a second counted once however many cover it."""
    total, reach = 0.0, start
    for span_start, span_end in sorted(spans):
        span_start, span_end = max(span_start, reach), min(span_end, end)
        if span_end > span_start:
            total += span_end - span_start
            reach = span_end
    return total


def flagged(project: str | None = None) -> Counter[str]:
    """What the flags leave of each passage: whether it may be used, and how much of it is clean.

    Two microphones flagging the same second have found one thing to cut, not
    two. Every passage gets both scores: no flags is a finding, not a gap.
    """
    passages = _passages(project)
    standing = flags.standing(passages)
    found: list[Score] = []
    for take_id, rows in passages.items():
        marks = standing.get(take_id)
        for passage_id, start, end in rows:
            inside = marks.within(start, end) if marks else []
            asked = any(mark.kind == "off_record" for mark in inside)
            cut = covered(((mark.start, mark.end) for mark in inside if mark.action == "cut"), start, end)
            found.append(("passage", passage_id, "publishable", 0.0 if asked else 1.0, None,
                          READ, "text", flags.PRODUCER))
            found.append(("passage", passage_id, "cleanliness", 1 - cut / max(end - start, 0.01), cut,
                          READ, "audio", flags.PRODUCER))
    return record(found)


def run(project: str | None = None, *, min_pause_s: float = 1.5) -> Counter[str]:
    """Score every passage and shot, or one project's, from what is already measured.

    Flags are redone first, so that no passage is scored as clean because
    nobody had looked.
    """
    scope = (project, project)
    with db.transaction():
        flags.scan(project=project, min_pause_s=min_pause_s)
        return (_write(_TEXT, scope) + recogniser(project) + _write(_SEEN, scope)
                + flagged(project) + _write(_SHOTS, scope))


def recipes() -> list[Recipe]:
    cur = db.cursor()
    cur.execute("SELECT name, coalesce(description, ''), subject_type, weights, min_modalities "
                "FROM media_video.score_recipes ORDER BY subject_type, name")
    return [Recipe(name, description, subject, {key: float(weight) for key, weight in weights.items()}, least)
            for name, description, subject, weights, least in cur.fetchall()]


def recipe(name: str) -> Recipe:
    for found in recipes():
        if found.name == name:
            return found
    raise EditError(f"no recipe is called {name!r}; `me rank` lists them")


def define(
    name: str, weights: dict[str, float], *, subject: str = "shot", description: str = "", min_modalities: int = 1,
) -> Recipe:
    """Make a recipe, or change the one of that name.

    A weight is how much a score counts beside the others. Only their
    proportions matter, and a thing missing one of the scores is ranked by
    the rest.
    """
    if subject not in ("passage", "shot"):
        raise EditError(f"a recipe ranks passages or shots, not {subject!r}")
    if not weights or any(weight <= 0 for weight in weights.values()):
        raise EditError("a recipe needs at least one score to weigh, each by more than nothing")
    if not 1 <= min_modalities <= 4:
        raise EditError("a recipe can ask for between one and four senses")
    cur = db.cursor()
    cur.execute(
        """INSERT INTO media_video.score_recipes (name, description, subject_type, weights, min_modalities)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (name) DO UPDATE SET
             description = EXCLUDED.description, subject_type = EXCLUDED.subject_type,
             weights = EXCLUDED.weights, min_modalities = EXCLUDED.min_modalities""",
        (name, description or None, subject, db.jsonb(weights), min_modalities),
    )
    return Recipe(name, description, subject, dict(weights), min_modalities)


def known(subject: str) -> set[str]:
    """The names of the scores that anything of one kind has."""
    cur = db.cursor()
    cur.execute("SELECT DISTINCT name FROM media_video.scores WHERE subject_type = %s", (subject,))
    return {name for (name,) in cur.fetchall()}


def ranked(
    name: str, *, limit: int | None = 25, min_coverage: float = 0.5, project: str | None = None,
    method: str | None = None, take: str | None = None, episode: str | None = None,
) -> Ranking:
    """The best of what a recipe ranks, best first, or all of it with no `limit`.

    `min_coverage` is how much of the recipe must have had a score: below it a
    figure rests on too little to set beside the others. For passages, each
    moment appears once, as the best passage that covers it, `method` keeps
    to passages found one way and `take` to one take's. Anything cut for good
    is left out, and so is anything cut from `episode` when one is named.
    """
    chosen = recipe(name)
    weight = sum(chosen.weights.values()) or 1.0
    cur = db.cursor()
    if chosen.subject == "passage":
        cur.execute(
            f"""SELECT v.subject_id, v.score, v.coverage, v.modalities, v.deepest_tier, v.contributing,
                      t.name, p.start_s, p.end_s, p.text, p.take_id
                 FROM media_video.v_combined v
                 JOIN media_video.passages p ON p.id = v.subject_id
                 JOIN media.takes t ON t.id = p.take_id
                WHERE v.recipe = %s AND v.subject_type = 'passage' AND v.coverage >= %s
                  AND (%s::text IS NULL OR t.project = %s) AND (%s::text IS NULL OR p.method = %s)
                  AND (%s::text IS NULL OR t.name = %s)
                  AND NOT EXISTS (SELECT 1 FROM media_video.exclusions x
                                   WHERE x.passage_id = p.id AND {_CUT})
                ORDER BY v.score DESC, p.start_s, v.subject_id""",
            (name, min_coverage * weight, project, project, method, method, take, take, episode),
        )
    elif chosen.subject == "shot":
        cur.execute(
            f"""SELECT v.subject_id, v.score, v.coverage, v.modalities, v.deepest_tier, v.contributing,
                      a.path, sh.start_s, sh.end_s, '', sh.asset_id
                 FROM media_video.v_combined v
                 JOIN media_video.shots sh ON sh.id = v.subject_id
                 JOIN media.assets a ON a.id = sh.asset_id
                WHERE v.recipe = %s AND v.subject_type = 'shot' AND v.coverage >= %s
                  AND (%s::text IS NULL OR a.project = %s)
                  AND NOT EXISTS (SELECT 1 FROM media_video.exclusions x
                                   WHERE (x.shot_id = sh.id OR x.asset_id = sh.asset_id) AND {_CUT})
                ORDER BY v.score DESC, a.path, sh.start_s""",
            (name, min_coverage * weight, project, project, episode),
        )
    else:
        raise EditError(f"recipe {name!r} ranks {chosen.subject}s, which nothing here can list")
    rows = cur.fetchall()
    held = flags.withheld(row[0] for row in rows) if chosen.subject == "passage" else set()

    entries: list[Entry] = []
    taken: dict[Any, list[tuple[float, float]]] = {}
    withheld = 0
    for subject_id, score, coverage, modalities, deepest, contributing, source, start, end, text, within in rows:
        if limit is not None and len(entries) >= limit:
            break
        start, end = float(start), float(end)
        spans = taken.setdefault(within, [])
        # Passages found different ways cover the same moments. Shots never do.
        if chosen.subject == "passage" and any(
                min(end, other_end) - max(start, other_start) > 0.3 * min(end - start, other_end - other_start)
                for other_start, other_end in spans):
            continue
        if str(subject_id) in held:
            withheld += 1
            continue
        spans.append((start, end))
        entries.append(Entry(subject_id, float(score), float(coverage) / weight, int(modalities),
                             int(deepest), tuple(contributing), source, start, end, text or ""))
    return Ranking(chosen, entries, withheld)
