"""Shot selection: picked to be good and unalike, then put in an order.

Taking the best shots by score gives the same shot twelve times: whatever
scores highest has neighbours that look like it and score like it. So a
compilation is chosen for two things at once. Each pick is the shot whose
quality, less how much it looks like anything already picked, is highest, and
the picks are then ordered so that the ones next to each other are the ones
least alike. How alike the neighbours ended up is measured on every run and
reported beside what taking the top of the list would have given, because a
selection that cannot say what it bought is a guess.

Quality is not worked out here. It is a recipe's ranking of the scores
already stored, so there is one place that says what a good shot is, and what
has been cut stays cut.

A selection is made of settings, and settings can be kept: a selector is a
set of them under a name, and every cut stores the ones it was made with. A
cut made after another reads what was said about that one. A clip that was
kept stays where it was, one that was adjusted stays with its new edges, and
one that was dropped is replaced and does not come back in any later revision.
"""

from __future__ import annotations

import heapq
import math
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from .. import db
from . import EditError, clock, scores, timelines
from .timelines import Clip

#: Shares of how alike two shots are: colour, brightness, movement.
HUE, VALUE, MOTION = 0.6, 0.2, 0.2
#: Movement below this is stillness: two locked-off shots are alike however
#: their noise compares. On the scale motion is measured on, 0 to 255.
STILL = 4.0
#: How far a seed moves each shot's quality, so that variants differ.
JITTER = 0.03


@dataclass(frozen=True)
class Look:
    """What a shot looks like, as far as likeness goes."""

    hue: tuple[float, ...]
    value: tuple[float, ...]
    motion: float


@dataclass(frozen=True)
class Shot:
    id: str
    asset_id: Any
    path: str
    start_s: float
    end_s: float
    quality: float
    look: Look
    #: When its sharpest sampled frame is, in the recording's time.
    peak_s: float | None = None


@dataclass(frozen=True)
class Options:
    """Everything a selection is made with."""

    recipe: str = "broll"
    match: str | None = None
    project: str | None = None
    count: int = 12
    #: How much being alike costs, against quality: 0 takes the best as they come.
    diversity: float = 0.5
    #: Use this many seconds of each shot, around its sharpest moment. None uses all of it.
    excerpt_s: float | None = None
    min_length_s: float = 1.0
    #: Order by file and by time within it, not by look.
    chronological: bool = False
    #: Only shots with somebody in them.
    people: bool = False
    min_coverage: float = 0.5


@dataclass(frozen=True)
class Selector:
    name: str
    description: str
    options: Options


@dataclass(frozen=True)
class Selection:
    clips: list[Clip]
    #: How alike neighbouring clips are, 0 to 1, averaged. None with fewer than two.
    likeness: float | None
    #: The same for the top of the ranking, taken as it comes.
    unselected: float | None
    considered: int
    #: Shots that ranked but have no signature to compare, and so were left out.
    unsigned: int = 0
    held: int = 0


@dataclass(frozen=True)
class Compiled:
    timeline: timelines.Timeline
    selection: Selection
    options: Options


def look(hue: Sequence[float], value: Sequence[float], motion: float | None) -> Look:
    """A stored signature, made ready to compare."""
    return Look(tuple(math.sqrt(max(share, 0.0)) for share in hue),
                tuple(math.sqrt(max(share, 0.0)) for share in value), float(motion or 0.0))


def similarity(one: Look, other: Look) -> float:
    """How alike two shots look, 0 to 1.

    Colour and brightness are compared as distributions, by how much they
    overlap. Movement is compared in proportion: twice as busy is as different
    between two slow shots as between two fast ones. Measured as a plain
    difference against a fixed span, over half of all pairs in a real library
    read as wholly unalike in movement, which told a selection nothing.
    """
    hue = sum(a * b for a, b in zip(one.hue, other.hue, strict=False))
    value = sum(a * b for a, b in zip(one.value, other.value, strict=False))
    moving = 1.0 - abs(one.motion - other.motion) / (max(one.motion, other.motion) + STILL)
    return HUE * min(hue, 1.0) + VALUE * min(value, 1.0) + MOTION * moving


def signatures(shot_ids: Iterable[Any]) -> dict[str, Look]:
    """The look of each shot that has one, by shot id."""
    cur = db.cursor()
    cur.execute(
        "SELECT shot_id, hue, value, motion FROM media_video.signatures WHERE shot_id = ANY(%s::uuid[])",
        ([str(shot_id) for shot_id in shot_ids],),
    )
    return {str(shot_id): look(hue, value, motion) for shot_id, hue, value, motion in cur.fetchall()}


def adjacent(looks: Sequence[Look | None]) -> float | None:
    """How alike neighbours are in a sequence, averaged over the pairs that can be compared."""
    found = [similarity(one, other) for one, other in zip(looks, looks[1:], strict=False) if one and other]
    return sum(found) / len(found) if found else None


def candidates(options: Options, *, episode: str | None = None) -> tuple[list[Shot], int]:
    """The shots a selection may draw on, best first, and how many had no signature."""
    if options.match is not None:
        try:
            wanted = re.compile(options.match)
        except re.error as error:
            raise EditError(f"{options.match!r} is not a pattern: {error}") from None
    ranking = scores.ranked(options.recipe, limit=None, min_coverage=options.min_coverage,
                            project=options.project, episode=episode)
    if ranking.recipe.subject != "shot":
        raise EditError(f"recipe {options.recipe!r} ranks {ranking.recipe.subject}s; a compilation is made of shots")
    entries = [entry for entry in ranking.entries
               if entry.end_s - entry.start_s >= options.min_length_s
               and (options.match is None or wanted.search(entry.source))]
    cur = db.cursor()
    cur.execute(
        """SELECT sh.id, sh.asset_id, coalesce(sh.is_black, false), coalesce(sh.subject_n, 0),
                  g.hue, g.value, g.motion,
                  (SELECT f.t_s FROM media_video.frames f
                    WHERE f.shot_id = sh.id AND f.sharpness IS NOT NULL
                    ORDER BY f.sharpness DESC, f.t_s LIMIT 1)
             FROM media_video.shots sh
             LEFT JOIN media_video.signatures g ON g.shot_id = sh.id
            WHERE sh.id = ANY(%s::uuid[])""",
        ([str(entry.subject_id) for entry in entries],),
    )
    known = {str(row[0]): row[1:] for row in cur.fetchall()}
    shots, unsigned = [], 0
    for entry in entries:
        asset_id, black, people, hue, value, motion, peak = known[str(entry.subject_id)]
        if black or (options.people and not people):
            continue
        if hue is None:
            unsigned += 1
            continue
        shots.append(Shot(str(entry.subject_id), asset_id, entry.source, entry.start_s, entry.end_s,
                          entry.score, look(hue, value, motion), peak))
    return shots, unsigned


def excerpt(shot: Shot, length_s: float | None) -> tuple[float, float]:
    """The stretch of a shot to use: all of it, or `length_s` around its sharpest moment."""
    if length_s is None or shot.end_s - shot.start_s <= length_s:
        return shot.start_s, shot.end_s
    middle = shot.peak_s if shot.peak_s is not None else (shot.start_s + shot.end_s) / 2
    start = min(max(middle - length_s / 2, shot.start_s), shot.end_s - length_s)
    return start, start + length_s


def choose(
    shots: Sequence[Shot], options: Options, *, seed: int | None = None,
    held: Sequence[tuple[int, Clip]] = (), unsigned: int = 0,
) -> Selection:
    """Pick `options.count` clips from `shots` and order them.

    `held` are clips already decided, each with the place it had (from 0).
    They stay, in those places, and count as picked when the rest are chosen.
    A `seed` nudges every shot's quality a little, the same way each time it
    is given, so two seeds give two cuts and one seed gives one.
    """
    if options.count < 1:
        raise EditError("a compilation needs at least one clip")
    if not 0 <= options.diversity <= 1:
        raise EditError(f"diversity runs from 0 to 1, and {options.diversity:g} is outside it")
    by_id = {shot.id: shot for shot in shots}
    held_looks = signatures(clip.shot_id for _, clip in held if clip.shot_id is not None) if held else {}
    taken = {str(clip.shot_id) for _, clip in held if clip.shot_id is not None}

    pool = [shot for shot in shots if shot.id not in taken]
    # A seed moves each shot by itself, so the same seed moves the same shot
    # the same way whatever else is in the running.
    quality = {shot.id: shot.quality + (random.Random(f"{seed}:{shot.id}").gauss(0.0, JITTER)
                                        if seed is not None else 0.0) for shot in pool}

    # What the top of the list would have been, for comparison.
    plain = sorted(shots, key=lambda shot: (-shot.quality, shot.path, shot.start_s))[:options.count]
    if options.chronological:
        plain.sort(key=lambda shot: (shot.path, shot.start_s))

    wanted = max(options.count - len(held), 0)
    picked: list[Shot] = []
    against = [held_looks[shot_id] for shot_id in sorted(taken) if shot_id in held_looks]
    # Each pick is the shot whose quality, less how much it looks like the
    # nearest thing already picked, is highest. That cost only ever grows, so
    # a shot's last worked-out worth is the most it can be now: the best one
    # on the heap is brought up to date, and if it is still ahead of the next
    # it is the pick, without the rest being looked at.
    heap = [(-quality[shot.id], shot.path, shot.start_s, shot.id, 0, 0.0) for shot in pool]
    heapq.heapify(heap)
    while heap and len(picked) < wanted:
        _, path, start, shot_id, seen, nearest = heapq.heappop(heap)
        shot = by_id[shot_id]
        if options.diversity:
            for other in (against + [one.look for one in picked])[seen:]:
                nearest = max(nearest, similarity(shot.look, other))
        worth = quality[shot_id] - options.diversity * nearest
        if heap and -heap[0][0] > worth:
            heapq.heappush(heap, (-worth, path, start, shot_id, len(against) + len(picked), nearest))
            continue
        picked.append(shot)

    def clip_of(shot: Shot) -> Clip:
        start, end = excerpt(shot, options.excerpt_s)
        return Clip(shot.asset_id, start, end, shot_id=shot.id, label=f"{Path(shot.path).name} {clock(start)}")

    def look_of(clip: Clip) -> Look | None:
        shot_id = str(clip.shot_id)
        return by_id[shot_id].look if shot_id in by_id else held_looks.get(shot_id)

    if options.chronological:
        placed = [clip for _, clip in held] + [clip_of(shot) for shot in picked]
        paths = {str(shot.asset_id): shot.path for shot in shots}
        if elsewhere := {str(clip.picture) for clip in placed} - set(paths):
            paths.update(_paths(elsewhere))
        placed.sort(key=lambda clip: (paths.get(str(clip.picture), ""), clip.start_s))
    else:
        total = len(held) + len(picked)
        slots: list[Clip | None] = [None] * total
        late = []
        for place, clip in sorted(held, key=lambda item: item[0]):
            if place < total and slots[place] is None:
                slots[place] = clip
            else:
                late.append(clip)
        free = list(picked)
        for place in range(total):
            if slots[place] is not None:
                continue
            if late:
                slots[place] = late.pop(0)
                continue
            around = [look_of(slots[near]) for near in (place - 2, place - 1, place + 1)
                      if 0 <= near < total and slots[near] is not None]
            around = [near for near in around if near is not None]
            # The first clip is the best one. After that, the one least like its neighbours.
            best = min(free, key=lambda shot: (max((similarity(shot.look, near) for near in around), default=0.0),
                                               -quality[shot.id]))
            free.remove(best)
            slots[place] = clip_of(best)
        placed = [clip for clip in slots if clip is not None]

    return Selection(placed, adjacent([look_of(clip) for clip in placed]),
                     adjacent([shot.look for shot in plain]), len(shots), unsigned, len(held))


def _paths(asset_ids: Iterable[Any]) -> dict[str, str]:
    cur = db.cursor()
    cur.execute("SELECT id, path FROM media.assets WHERE id = ANY(%s::uuid[])",
                ([str(asset_id) for asset_id in asset_ids],))
    return {str(asset_id): path for asset_id, path in cur.fetchall()}


def remember(name: str, options: Options, description: str = "") -> Selector:
    """Keep a set of options under a name, in place of any that had it."""
    stored = asdict(options)
    match = stored.pop("match")
    cur = db.cursor()
    cur.execute(
        """INSERT INTO media_video.selectors (name, description, match, params)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, match = EXCLUDED.match,
                                            params = EXCLUDED.params, updated_at = now()""",
        (name, description or None, match, db.jsonb(stored)),
    )
    return Selector(name, description, options)


def selector(name: str) -> Selector:
    cur = db.cursor()
    cur.execute("SELECT name, description, match, params FROM media_video.selectors WHERE name = %s", (name,))
    if not (found := cur.fetchone()):
        raise EditError(f"no selector is called {name!r}; `me selectors` lists them")
    return _selector(*found)


def selectors() -> list[Selector]:
    cur = db.cursor()
    cur.execute("SELECT name, description, match, params FROM media_video.selectors ORDER BY name")
    return [_selector(*row) for row in cur.fetchall()]


def _selector(name: str, description: str | None, match: str | None, params: dict[str, Any] | None) -> Selector:
    return Selector(name, description or "", _options({**(params or {}), "match": match}))


def _options(stored: dict[str, Any], base: Options | None = None) -> Options:
    """Options from stored settings, ignoring any this version does not know."""
    known = {field.name for field in fields(Options)}
    return replace(base or Options(), **{key: value for key, value in stored.items() if key in known})


def compilation(
    name: str, *, using: str | None = None, after: str | None = None, seed: int | None = None,
    episode: str | None = None, title: str | None = None, **changes: Any,
) -> Compiled:
    """Choose shots and store them as the cut called `name`.

    Settings come from the selector named by `using`, or else from the cut
    this one is made `after`, and then from `changes`. What was said about
    the clips of the cut before is honoured either way.
    """
    options, feedback, selector_name = Options(), timelines.Feedback(), using
    if after is not None:
        parent = timelines.load(after)
        options = _options(parent.params.get("options", {}), options)
        feedback = timelines.feedback(after)
        selector_name = using or parent.selector
        episode = episode or parent.episode
    if using is not None:
        options = selector(using).options
    unknown = set(changes) - {field.name for field in fields(Options)}
    if unknown:
        raise EditError(f"a selection has no setting called {', '.join(sorted(unknown))}")
    options = replace(options, **{key: value for key, value in changes.items() if value is not None})

    shots, unsigned = candidates(options, episode=episode)
    shots = [shot for shot in shots if shot.id not in feedback.dropped]
    available = {shot.id for shot in shots}
    held = [(place, clip) for place, clip in feedback.held
            if clip.shot_id is None or str(clip.shot_id) in available or not _cut(clip.shot_id, episode)]
    if not shots and not held:
        raise EditError(
            f"nothing to choose from: recipe {options.recipe!r} ranks no shots"
            + (f" matching {options.match!r}" if options.match else "")
            + (f" ({unsigned} have no signature; `me signatures` gives them one)" if unsigned
               else ". `me score` scores what has been analysed")
        )
    selection = choose(shots, options, seed=seed, held=held, unsigned=unsigned)
    stored = timelines.save(
        name, selection.clips, kind="compilation", project=options.project, title=title,
        params={"options": asdict(options), "likeness": selection.likeness, "unselected": selection.unselected},
        seed=seed, after=after, selector=selector_name, episode=episode,
    )
    return Compiled(stored, selection, options)


def _cut(shot_id: Any, episode: str | None) -> bool:
    """Whether a shot has been ruled out, for good or for this episode."""
    cur = db.cursor()
    cur.execute(
        """SELECT EXISTS (
             SELECT 1 FROM media_video.exclusions x JOIN media_video.shots sh ON sh.id = %s
              WHERE (x.shot_id = sh.id OR x.asset_id = sh.asset_id)
                AND (x.episode_id IS NULL OR x.episode_id = (SELECT id FROM media.episodes WHERE slug = %s)))""",
        (shot_id, episode),
    )
    return bool(cur.fetchone()[0])
