"""A cut of an interview, and other footage laid over it.

A passage is a stretch of a take's time, and a cut is made of files. Going
from one to the other is three choices, made here for every passage:

- the picture comes from the take's main camera if it was rolling, and
  otherwise from another camera that was
- the sound comes from a microphone before it comes from a camera, and among
  microphones from the one that measured cleanest. Where nothing has measured
  them, it comes from the one whose recogniser was surest of the most words,
  which is the one the speaker was nearest
- the ends are pulled in to the first and last word, as far as the style of
  the cut allows

What a person kept goes in first, whatever it scored. The ranking then fills
the cut to its length, and every passage that is passed over is passed over
for a reason that is given. Only the ends of a passage move: a filler in the
middle of one stays where it was said.

`cutaways` lays other footage over parts of a cut while its sound carries on.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from .. import db
from ..takes import Member, find, members
from . import EditError, clock, decisions, flags, scores, select, timelines
from .quality import certainty
from .said import Line, Spans, lines, tokens
from .timelines import Clip, Timeline

#: A passage that stops in the middle of a sentence can still be used if the
#: speaker paused there for this long: the pause is where the cut goes.
PAUSE_S = 0.45
#: Two stretches of one recording are the same moment when they share more
#: than this much of the shorter.
SAME = 0.3
#: A cut may run this far past its length before a passage is passed over for
#: a shorter one.
OVER = 1.1
#: A signal-to-noise figure outside this is a measurement that failed, and
#: counts as none.
PLAUSIBLE_DB = (-20.0, 45.0)
LABEL = 120


@dataclass(frozen=True)
class Style:
    """The manner of a cut: how long, how fast, and how much is laid over it."""

    name: str | None = None
    description: str = ""
    target_s: float = 300.0
    min_passage_s: float = 12.0
    max_passage_s: float = 95.0
    min_words: int = 80
    #: No more quiet than this is left at either end of a passage.
    strip_silence_over_s: float = 1.5
    #: In the order it was said, not best first.
    chronological: bool = True
    #: How many of the clips get other footage laid over them, 0 to 1.
    broll_fraction: float = 0.25


@dataclass(frozen=True)
class Part:
    """One passage as a cut uses it. Times are in the take's."""

    passage_id: Any
    take: str
    start_s: float
    end_s: float
    picture: str
    #: The recording the sound is from, or None when it is the picture's own.
    sound: str | None
    #: What the recipe made of it, or None when a person named it.
    score: float | None
    kept: bool
    clip: Clip


@dataclass(frozen=True)
class Left:
    """A passage that was passed over, and why."""

    passage_id: Any
    take: str
    start_s: float
    end_s: float
    why: str


@dataclass(frozen=True)
class Assembled:
    timeline: Timeline
    parts: list[Part]
    left: list[Left]
    #: How many passages were looked at before the cut was full.
    considered: int
    #: Passages left out because something in them was said off the record.
    withheld: int = 0
    #: Clips brought along from the cut before.
    held: int = 0


@dataclass(frozen=True)
class Covered:
    timeline: Timeline
    #: How many clips have other footage over them, of how many were meant to.
    covered: int
    wanted: int
    #: How many shots there were to lay over them.
    available: int
    #: How alike one cutaway is to the next, 0 to 1, averaged. None with fewer than two.
    likeness: float | None = None


@dataclass(frozen=True)
class _Measured:
    start: float
    end: float
    asset_id: Any
    snr_db: float


@dataclass(frozen=True)
class _Passage:
    id: Any
    take_id: Any
    take: str
    start: float
    end: float
    text: str
    words: int
    whole: bool
    score: float | None = None
    kept: bool = False
    note: str | None = None


def styles() -> list[Style]:
    """Every stored style, by name."""
    cur = db.cursor()
    cur.execute("SELECT name, description, params FROM media_video.styles ORDER BY name")
    return [_style(*row) for row in cur.fetchall()]


def style(name: str) -> Style:
    """The stored style called `name`."""
    cur = db.cursor()
    cur.execute("SELECT name, description, params FROM media_video.styles WHERE name = %s", (name,))
    if not (found := cur.fetchone()):
        raise EditError(f"no style is called {name!r}; `media styles` lists them")
    return _style(*found)


def shaped(name: str) -> Style:
    """The manner the cut called `name` was assembled in."""
    kept = timelines.load(name).params.get("style")
    return _style(kept.get("name"), None, kept) if isinstance(kept, dict) else Style()


def _style(name: str | None, description: str | None, params: dict[str, Any] | None) -> Style:
    known = {field.name for field in fields(Style)} - {"name", "description"}
    return Style(name, description or "", **{key: value for key, value in (params or {}).items()
                                              if key in known and value is not None})


def covers(member: Member, start: float, end: float) -> bool:
    """Whether a recording was running for the whole of a stretch of its take."""
    if not member.placed:
        return False
    began = start - (member.offset_s or 0.0)
    return began >= 0 and (member.duration_s is None or end - (member.offset_s or 0.0) <= member.duration_s)


def camera(cast: Sequence[Member], start: float, end: float) -> Member | None:
    """The camera to see a stretch through: the take's main one if it was rolling, else the first that was."""
    return next((member for member in cast if member.medium == "video" and covers(member, start, end)), None)


def tighten(heard: Spans[Line], start: float, end: float, quiet_s: float) -> tuple[float, float]:
    """Pull the ends of a stretch in until no more than `quiet_s` of quiet is left at either.

    The first and last word are looked for on every recording in the take, so
    a word one microphone missed still holds its end open. Only the ends
    move, and only inward.
    """
    first, last = None, None
    for line in heard.within(start, end):
        for began, ended, _ in line.words or ((line.start, line.end, line.text),):
            if ended > start and began < end:
                first = began if first is None else min(first, began)
                last = ended if last is None else max(last, ended)
    if first is None or last is None:
        return start, end
    return max(start, first - quiet_s), min(end, last + quiet_s)


def sure(heard: Iterable[Line], start: float, end: float) -> dict[Any, float]:
    """How many words each recording heard in a stretch, each for as much as its recogniser was sure of it.

    A word counts when its middle falls inside. A line with no word timings
    counts for as much of itself as is inside.
    """
    found: dict[Any, float] = {}
    for line in heard:
        if line.words:
            words = sum(1 for began, ended, _ in line.words if start <= (began + ended) / 2 < end)
        else:
            inside = min(line.end, end) - max(line.start, start)
            words = len(tokens(line.text)) * max(inside, 0.0) / max(line.length, 0.01)
        if words > 0:
            found[line.asset_id] = found.get(line.asset_id, 0.0) + words * certainty(line.logprob, line.no_speech)
    return found


def clean(measured: Spans[_Measured], start: float, end: float) -> dict[Any, float]:
    """Each recording's signal over its noise through a stretch, in decibels, where that was measured."""
    totals: dict[Any, list[float]] = {}
    for found in measured.within(start, end):
        inside = min(found.end, end) - max(found.start, start)
        if inside > 0:
            total = totals.setdefault(found.asset_id, [0.0, 0.0])
            total[0] += inside
            total[1] += inside * found.snr_db
    return {asset_id: weighed / inside for asset_id, (inside, weighed) in totals.items()}


def microphone(
    cast: Sequence[Member], heard: Spans[Line], start: float, end: float,
    measured: Mapping[Any, float] | None = None,
) -> Member | None:
    """The recording to take a stretch's sound from, or None when none heard a word of it.

    A microphone is put near a mouth and a camera is not, so a camera's sound
    is used only when no microphone was running. Measured noise decides
    between recordings when every one of them has been measured: a
    measurement cannot be set against the lack of one.
    """
    words = sure(heard.within(start, end), start, end)
    able = [member for member in cast
            if member.has_audio and covers(member, start, end) and words.get(member.asset_id, 0.0) > 0]
    if any(member.medium == "audio" for member in able):
        able = [member for member in able if member.medium == "audio"]
    if not able:
        return None
    if measured and all(member.asset_id in measured for member in able):
        return max(able, key=lambda member: (measured[member.asset_id], words[member.asset_id]))
    return max(able, key=lambda member: words[member.asset_id])


class _Takes:
    """What assembling needs to know of each take, read once."""

    def __init__(self) -> None:
        self.casts: dict[Any, list[Member]] = {}
        self.heard: dict[Any, Spans[Line]] = {}
        self.measured: dict[Any, Spans[_Measured]] = {}

    def of(self, take_id: Any) -> tuple[list[Member], Spans[Line], Spans[_Measured]]:
        if take_id not in self.casts:
            self.casts[take_id] = members(take_id)
            self.heard[take_id] = Spans(lines(take_id, with_words=True))
            cur = db.cursor()
            cur.execute(
                """SELECT s.start_s + ta.offset_s, s.end_s + ta.offset_s, s.asset_id, q.snr_db
                     FROM media.take_assets ta
                     JOIN media_video.segments s ON s.asset_id = ta.asset_id
                     JOIN media_video.audio_quality q ON q.segment_id = s.id
                    WHERE ta.take_id = %s AND (ta.is_primary OR ta.offset_method IS NOT NULL)
                      AND q.snr_db BETWEEN %s AND %s""",
                (take_id, *PLAUSIBLE_DB),
            )
            self.measured[take_id] = Spans(_Measured(float(start), float(end), asset_id, float(snr))
                                           for start, end, asset_id, snr in cur.fetchall())
        return self.casts[take_id], self.heard[take_id], self.measured[take_id]


def _passages(ids: Sequence[Any]) -> dict[str, _Passage]:
    cur = db.cursor()
    cur.execute(
        """SELECT p.id, p.take_id, t.name, p.start_s, p.end_s, p.text, p.n_words,
                  q.ends_complete, q.tail_gap_s, q.starts_complete, q.head_gap_s
             FROM media_video.passages p
             JOIN media.takes t ON t.id = p.take_id
             LEFT JOIN media_video.passage_quality q ON q.passage_id = p.id
            WHERE p.id = ANY(%s::uuid[])""",
        ([str(passage_id) for passage_id in ids],),
    )
    found = {}
    for passage_id, take_id, take, start, end, text, words, ends, tail, starts, head in cur.fetchall():
        whole = ((ends is None or ends or (tail or 0.0) >= PAUSE_S)
                 and (starts is None or starts or (head or 0.0) >= PAUSE_S))
        found[str(passage_id)] = _Passage(passage_id, take_id, take, float(start), float(end), text or "",
                                          int(words or 0), whole)
    return found


def _kept(take: str | None, project: str | None, episode: str | None) -> list[Any]:
    """The passages a person last ruled to keep, in the order they were said."""
    cur = db.cursor()
    cur.execute(
        f"""SELECT p.id
              FROM media_video.passages p
              JOIN media.takes t ON t.id = p.take_id
             CROSS JOIN LATERAL (SELECT d.keep FROM media_video.decisions d
                                  WHERE d.kind = 'passage_keep' AND d.source_of = 'human' AND d.passage_id = p.id
                                  ORDER BY d.decided_at DESC, d.id DESC LIMIT 1) last
             WHERE last.keep AND (%s::text IS NULL OR t.name = %s) AND (%s::text IS NULL OR t.project = %s)
               AND NOT EXISTS (SELECT 1 FROM media_video.exclusions x
                                WHERE x.passage_id = p.id AND {scores._CUT})
             ORDER BY t.name, p.start_s, p.id""",
        (take, take, project, project, episode),
    )
    return [row[0] for row in cur.fetchall()]


def _same(spans: Iterable[tuple[str, float, float]], asset_id: Any, start: float, end: float) -> bool:
    return any(str(asset_id) == other and min(end, other_end) - max(start, other_start)
               > SAME * min(end - start, other_end - other_start)
               for other, other_start, other_end in spans)


def _stretches(clip: Clip) -> list[tuple[str, float, float]]:
    """The recordings a clip reads, and when in each."""
    found = [(str(clip.picture), clip.start_s, clip.end_s)]
    if clip.sound is not None and clip.sound_start_s is not None and clip.sound_end_s is not None:
        found.append((str(clip.sound), clip.sound_start_s, clip.sound_end_s))
    return found


def _weave(held: Sequence[tuple[int, Clip]], rest: Sequence[Clip]) -> list[Clip]:
    """Clips brought along go back where they were, and the rest flow around them in order.

    One whose place is past the end of a cut that came out shorter goes last.
    """
    slots: list[Clip | None] = [None] * (len(held) + len(rest))
    late = []
    for place, clip in sorted(held, key=lambda item: item[0]):
        if place < len(slots) and slots[place] is None:
            slots[place] = clip
        else:
            late.append(clip)
    flowing = iter([*rest, *late])
    return [slot if slot is not None else next(flowing) for slot in slots]


def interview(
    name: str, shape: Style | None = None, *, take: str | None = None, project: str | None = None,
    episode: str | None = None, recipe: str | None = None, method: str | None = None,
    per_take: int | None = None, only: Sequence[Any] | None = None, after: str | None = None,
    title: str | None = None, min_coverage: float = 0.5, canvas: str | None = None, fit: str | None = None,
) -> Assembled:
    """Assemble a cut from passages and store it as `name`.

    The passages are one take's, one project's, or everybody's, best first by
    `recipe`, after the ones a person kept. `only` names the passages
    instead, and then they are used as they are, in the order given: nothing
    is weighed and nothing is too long. Made `after` another cut, the clips
    that were kept or adjusted there stay where they were, what was dropped
    from it is not picked again, and whatever is not said here is as it was
    said there.
    """
    feedback = timelines.Feedback()
    if after is not None:
        parent = timelines.load(after)
        feedback = timelines.feedback(after)
        before = parent.params
        episode = episode or parent.episode
        if take is None and project is None:
            take, project = before.get("take"), before.get("project")
        recipe = recipe or before.get("recipe")
        method = method or before.get("method")
        per_take = per_take or before.get("per_take")
        shape = shape or shaped(after)
        title, canvas, fit = title or parent.title, canvas or parent.canvas, fit or parent.fit
    shape = shape or Style()
    recipe = recipe or "interview"
    belongs_to = project or (find(take).project if take is not None else None)

    withheld = 0
    if only is not None:
        ids = [decisions.find(given, "passage").id for given in only]
        known = _passages(ids)
        running = [replace(known[str(passage_id)], kept=True) for passage_id in dict.fromkeys(ids)]
    else:
        ranking = scores.ranked(recipe, limit=None, min_coverage=min_coverage, project=project, method=method,
                                take=take, episode=episode)
        if ranking.recipe.subject != "passage":
            raise EditError(f"recipe {recipe!r} ranks {ranking.recipe.subject}s; an interview is cut from passages")
        withheld = ranking.withheld
        kept = _kept(take, project, episode)
        known = _passages([*kept, *(entry.subject_id for entry in ranking.entries)])
        notes = decisions.standing(kept)
        scored = {str(entry.subject_id): entry.score for entry in ranking.entries}
        running = [replace(known[str(passage_id)], kept=True, score=scored.get(str(passage_id)),
                           note=notes.get(str(passage_id), (None, None))[1]) for passage_id in kept]
        running += [replace(known[str(entry.subject_id)], score=entry.score) for entry in ranking.entries
                    if str(entry.subject_id) not in {str(passage_id) for passage_id in kept}]
    asked_not_to = flags.withheld(passage.id for passage in running if passage.kept)

    held = list(feedback.held)
    used = [stretch for _, clip in held for stretch in _stretches(clip)]
    total = sum(clip.duration_s for _, clip in held)
    share = None
    if only is None and len({passage.take_id for passage in running}) > 1:
        share = per_take or max(2, int(shape.target_s / 30))
    taken: Counter[Any] = Counter()
    known_takes = _Takes()
    parts: list[Part] = []
    left: list[Left] = []
    considered = 0

    for passage in running:
        weighed = not passage.kept
        if weighed and total >= shape.target_s:
            break
        considered += 1
        length = passage.end - passage.start
        why = None
        if str(passage.id) in asked_not_to:
            why = "holding something said off the record"
        elif weighed and length < shape.min_passage_s:
            why = "too short"
        elif weighed and length > shape.max_passage_s:
            why = "too long"
        elif weighed and passage.words < shape.min_words:
            why = "too few words"
        elif weighed and not passage.whole:
            why = "starting or stopping in the middle of a sentence"
        elif weighed and share is not None and taken[passage.take_id] >= share:
            why = "from a take that has given its share"
        if why is None:
            cast, heard, measured = known_takes.of(passage.take_id)
            start, end = tighten(heard, passage.start, passage.end, shape.strip_silence_over_s)
            seen = camera(cast, start, end)
            if seen is None:
                why = "with no camera rolling"
        if why is None:
            listened = microphone(cast, heard, start, end, clean(measured, start, end))
            apart = listened is not None and listened.asset_id != seen.asset_id
            clip = Clip(
                seen.asset_id, start - (seen.offset_s or 0.0), end - (seen.offset_s or 0.0),
                sound=listened.asset_id if apart else None,
                sound_start_s=start - (listened.offset_s or 0.0) if apart else None,
                sound_end_s=end - (listened.offset_s or 0.0) if apart else None,
                label=" ".join(passage.text.split())[:LABEL] or None, note=passage.note,
            )
            stretches = _stretches(clip)
            if any(_same(feedback.spans, *stretch) for stretch in stretches[:1]):
                why = f"dropped from {after} or a cut before it"
            elif any(_same(used, *stretch) for stretch in stretches):
                why = "of a moment the cut already has"
            elif weighed and (parts or held) and total + (end - start) > shape.target_s * OVER:
                why = "that would run past the length"
        if why is not None:
            left.append(Left(passage.id, passage.take, passage.start, passage.end, why))
            continue
        used += stretches
        total += end - start
        taken[passage.take_id] += 1
        parts.append(Part(passage.id, passage.take, start, end, Path(seen.label).name,
                          Path(listened.label).name if apart else None, passage.score, passage.kept, clip))

    if not parts and not held:
        reasons = Counter(passed.why for passed in left)
        raise EditError(
            f"nothing to make {name!r} from: "
            + ("every passage looked at was passed over ("
               + ", ".join(f"{count} {why}" for why, count in reasons.most_common()) + ")" if considered
               else f"recipe {recipe!r} ranks no passages here"
               + (f" ({withheld} left out for something said off the record)" if withheld else "")
               + ". `media passages` finds them and `media score` scores them")
        )
    in_order = shape.chronological and only is None
    ordered = sorted(parts, key=lambda part: (part.take, part.start_s)) if in_order else parts
    stored = timelines.save(
        name, _weave(held, [part.clip for part in ordered]), kind="cut",
        project=belongs_to, title=title, after=after, episode=episode, canvas=canvas, fit=fit,
        style=shape.name if shape.name in {known_style.name for known_style in styles()} else None,
        params={"recipe": recipe, "take": take, "project": project, "method": method, "per_take": per_take,
                "style": {key: value for key, value in asdict(shape).items() if key != "description"}},
    )
    return Assembled(stored, ordered, left, considered, withheld, len(held))


def _spread(places: Sequence[int], count: int) -> list[int]:
    """`count` of these places, evenly apart."""
    count = min(count, len(places))
    return [places[int((at + 0.5) * len(places) / count)] for at in range(count)]


def cutaways(
    name: str, over: str, *, fraction: float | None = None, recipe: str = "broll", match: str | None = None,
    project: str | None = None, shortest_s: float = 2.5, longest_s: float = 6.0, lead_s: float = 2.0,
    diversity: float = 0.5, title: str | None = None,
) -> Covered:
    """Lay other footage over some of a cut's clips, and store the result as `name`.

    The sound of the cut is not touched. A clip no longer than `longest_s` is
    covered whole when there is a shot long enough, which hides the jump
    from the clip before; the first clip never is, so that whoever is
    talking is seen before they are talked over. A longer clip keeps
    `lead_s` of the speaker at each end and is covered in between.

    A cut that already has footage over it gets no more unless a `fraction`
    is asked for, and each cutaway that was dropped from it is replaced by
    another shot of the same length.

    The footage is ranked by `recipe`, and each cutaway is the best that is
    least like the one before. Shots with somebody filling the frame are
    left out, since their lips would move to other words, and so is anything
    recorded in the takes the cut is made from.
    """
    cut = timelines.load(over)
    shape = cut.params.get("style") or {}
    if fraction is None:
        covered = any(clip.shot_id is not None for clip in cut.clips)
        fraction = 0.0 if covered else float(shape.get("broll_fraction", Style.broll_fraction))
    if not 0 <= fraction <= 1:
        raise EditError(f"the share of clips to cover runs from 0 to 1, and {fraction:g} is outside it")
    if not 0 < shortest_s <= longest_s:
        raise EditError("a cutaway's shortest length has to be above nothing and no more than its longest")

    def room(index: int, clip: Clip) -> float:
        """How much of a clip could be covered."""
        if clip.shot_id is not None:
            return 0.0
        if index and clip.duration_s <= longest_s:
            return clip.duration_s
        return min(longest_s, clip.duration_s - 2 * lead_s)

    open_to = [index for index, clip in enumerate(cut.clips) if room(index, clip) >= shortest_s]
    # Any share at all is at least one clip.
    wanted = max(1, round(fraction * len(cut.clips))) if fraction > 0 else 0
    places = _spread(open_to, wanted) if wanted and open_to else []
    said = timelines.reactions(over)
    again = {index for index, clip in enumerate(cut.clips)
             if clip.shot_id is not None and index + 1 in said and said[index + 1].verdict == "drop"}

    feedback = timelines.feedback(over)
    shots, _ = select.candidates(select.Options(recipe=recipe, match=match, project=project,
                                                 min_length_s=shortest_s), episode=cut.episode)
    # The recordings the talking is in: every clip's sound, and its picture unless that is a cutaway.
    talking = {asset_id for clip in cut.clips
               for asset_id, _, _ in _stretches(clip)[1 if clip.shot_id is not None else 0:]}
    cur = db.cursor()
    cur.execute(
        """SELECT DISTINCT other.asset_id
             FROM media.take_assets mine
             JOIN media.take_assets other ON other.take_id = mine.take_id
            WHERE mine.asset_id = ANY(%s::uuid[])""",
        (sorted(talking),),
    )
    theirs = {str(row[0]) for row in cur.fetchall()} | talking
    cur.execute("SELECT shot_id FROM media_video.v_shot_content WHERE shot_id = ANY(%s::uuid[]) "
                "AND safe_over_other_audio", ([shot.id for shot in shots],))
    safe = {str(row[0]) for row in cur.fetchall()}
    already = {str(clip.shot_id) for clip in cut.clips if clip.shot_id is not None}
    pool = [shot for shot in shots if shot.id in safe and shot.id not in already
            and shot.id not in feedback.dropped and str(shot.asset_id) not in theirs]

    clips = list(cut.clips)
    looks: list[select.Look] = []
    laid: dict[int, list[Clip]] = {}
    for index in sorted({*places, *again}):
        clip = clips[index]
        space = clip.duration_s if index in again else room(index, clip)
        whole = index in again or (bool(index) and clip.duration_s <= longest_s)
        able = [shot for shot in pool if shot.end_s - shot.start_s >= (space if whole else shortest_s)]
        if not able:
            continue
        # The best first, so that of two worth the same the better ranked is taken.
        best = max(able, key=lambda shot: shot.quality - diversity * (select.similarity(shot.look, looks[-1])
                                                                      if looks else 0.0))
        pool.remove(best)
        looks.append(best.look)
        length = min(space, best.end_s - best.start_s)
        begins = 0.0 if whole else (clip.duration_s - length) / 2
        start, end = select.excerpt(best, length)
        own = clip.sound is None or clip.sound_start_s is None
        sound, sound_start = (clip.picture, clip.start_s) if own else (clip.sound, clip.sound_start_s)
        over_it = Clip(best.asset_id, start, end, sound=sound, sound_start_s=sound_start + begins,
                       sound_end_s=sound_start + begins + length, shot_id=best.id,
                       label=f"{Path(best.path).name} {clock(start)}", note="over what is being said")
        before = replace(clip, end_s=clip.start_s + begins,
                         sound_end_s=None if own else clip.sound_start_s + begins)
        after = replace(clip, start_s=clip.start_s + begins + length,
                        sound_start_s=None if own else clip.sound_start_s + begins + length)
        laid[index] = [part for part in (before, over_it, after) if part.end_s - part.start_s > 0.001]

    if not laid:
        raise EditError(
            f"nothing was laid over {over!r}: "
            + ("it was asked to cover none of its clips" if not wanted and not again
               else "none of its clips has room for a cutaway" if not open_to and not again
               else f"recipe {recipe!r} ranks no shot that is long enough, has nobody filling the frame and "
                    f"was not recorded in the same take. `media score` scores what has been analysed")
        )
    stored = timelines.save(
        name, [part for index, clip in enumerate(clips) for part in laid.get(index, [clip])], kind="mixed",
        project=cut.project, title=title or cut.title, after=None if name == over else over,
        episode=cut.episode, canvas=cut.canvas, fit=cut.fit, style=cut.style, seed=cut.seed,
        params={**cut.params, "cutaways": {"recipe": recipe, "match": match, "project": project,
                                           "fraction": fraction, "shortest_s": shortest_s,
                                           "longest_s": longest_s, "lead_s": lead_s, "diversity": diversity}},
    )
    return Covered(stored, len(laid), len(places) + len(again), len(pool) + len(laid), select.adjacent(looks))
