"""Timelines: a cut, kept as rows.

A cut is a list of clips in order. Each clip is a stretch of one recording's
picture and, when the sound comes from somewhere else, a stretch of another
recording's sound: holding an answer on the sound while the picture cuts away
is the commonest move in a documentary, so the two are stored apart.

Kept as rows, a cut can be asked things a file cannot: what it was made from,
which cut it was a revision of, what was said about each clip. That last one
is what makes cutting a loop. A reaction to a clip is stored against the clip,
and the next cut made after this one reads them: what was dropped stays out
of every later revision, what was kept comes along, and "right shot, half a
second late" moves the clip without losing the choice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .. import db
from . import EditError

KINDS = ("cut", "compilation", "script", "mixed")
STATUSES = ("draft", "review", "approved", "discarded")
VERDICTS = ("keep", "drop", "adjust")


@dataclass(frozen=True)
class Clip:
    """One stretch of a cut. Times are in each recording's own time."""

    picture: Any
    start_s: float
    end_s: float
    #: The recording the sound comes from, when it is not the picture's own.
    sound: Any = None
    sound_start_s: float | None = None
    sound_end_s: float | None = None
    shot_id: Any = None
    segment_id: Any = None
    label: str | None = None
    note: str | None = None

    @property
    def duration_s(self) -> float:
        """How long it plays: as long as its sound when that is separate."""
        if self.sound is not None and self.sound_start_s is not None and self.sound_end_s is not None:
            return self.sound_end_s - self.sound_start_s
        return self.end_s - self.start_s


@dataclass(frozen=True)
class Reaction:
    """The last thing said about one clip."""

    verdict: str
    shift_s: float = 0.0
    extend_s: float = 0.0
    note: str | None = None


@dataclass(frozen=True)
class Timeline:
    id: Any
    name: str
    kind: str
    status: str
    revision: int
    clips: list[Clip]
    project: str | None = None
    title: str | None = None
    seed: int | None = None
    #: The cut this one was made after.
    after: str | None = None
    selector: str | None = None
    episode: str | None = None
    canvas: str = "landscape"
    fit: str = "pad"
    params: dict[str, Any] = field(default_factory=dict)
    #: The manner it was cut in, when it was cut in a stored one.
    style: str | None = None

    @property
    def duration_s(self) -> float:
        return sum(clip.duration_s for clip in self.clips)


@dataclass(frozen=True)
class Summary:
    name: str
    kind: str
    status: str
    revision: int
    clips: int
    duration_s: float
    dropped: int
    adjusted: int
    episode: str | None = None
    style: str | None = None


@dataclass(frozen=True)
class Feedback:
    """What the cuts before this one were told."""

    #: Shots dropped from this cut or any it came from.
    dropped: frozenset[str] = frozenset()
    #: Clips to bring along from the cut just before, moved where asked, each
    #: with the place it had there, counted from 0.
    held: tuple[tuple[int, Clip], ...] = ()
    #: Every stretch dropped the same way, as the recording it was of and when in it.
    spans: tuple[tuple[str, float, float], ...] = ()


def episode_id(slug: str, *, create: bool = False, project: str | None = None) -> Any:
    """The episode called `slug`. Saving a cut for one that is new begins it."""
    cur = db.cursor()
    cur.execute("SELECT id FROM media.episodes WHERE slug = %s", (slug,))
    if found := cur.fetchone():
        return found[0]
    if not create:
        raise EditError(f"no episode is called {slug!r}")
    cur.execute("INSERT INTO media.episodes (slug, project) VALUES (%s, %s) RETURNING id", (slug, project))
    return cur.fetchone()[0]


def save(
    name: str, clips: list[Clip], *, kind: str = "cut", project: str | None = None, title: str | None = None,
    logline: str | None = None, params: dict[str, Any] | None = None, seed: int | None = None,
    after: str | None = None, selector: str | None = None, episode: str | None = None,
    canvas: str | None = None, fit: str | None = None, style: str | None = None,
) -> Timeline:
    """Store a cut under a name, in place of a draft that had it.

    `after` names the cut this one revises. A cut that has been approved, or
    that someone has reacted to, is not written over: what was said about it
    is the record the next revision is made from.
    """
    if not clips:
        raise EditError(f"{name!r} has no clips, and a cut needs at least one")
    if kind not in KINDS:
        raise EditError(f"a timeline is one of {', '.join(KINDS)}, not {kind!r}")
    with db.transaction():
        cur = db.cursor()
        parent_id, revision = None, 1
        if after is not None:
            if after == name:
                raise EditError(f"{name!r} cannot come after itself; give the revision a name of its own")
            cur.execute("SELECT id, revision FROM media_video.timelines WHERE name = %s", (after,))
            if not (parent := cur.fetchone()):
                raise EditError(f"no timeline is called {after!r}")
            parent_id, revision = parent[0], parent[1] + 1
        cur.execute(
            """SELECT t.id, t.status,
                      EXISTS (SELECT 1 FROM media_video.clip_notes n
                                JOIN media_video.timeline_clips c ON c.id = n.clip_id
                               WHERE c.timeline_id = t.id)
                 FROM media_video.timelines t WHERE t.name = %s""",
            (name,),
        )
        if standing := cur.fetchone():
            timeline_id, status, reacted = standing
            if status == "approved" or reacted:
                raise EditError(
                    f"{name!r} {'is approved' if status == 'approved' else 'has reactions on its clips'} "
                    f"and is not written over. Save this under another name, after {name!r}."
                )
            cur.execute("DELETE FROM media_video.timeline_clips WHERE timeline_id = %s", (timeline_id,))
        else:
            cur.execute("INSERT INTO media_video.timelines (name) VALUES (%s) RETURNING id", (name,))
            timeline_id = cur.fetchone()[0]
        cur.execute(
            """UPDATE media_video.timelines SET
                 kind = %s, project = %s, title = %s, logline = %s, params = %s, seed = %s,
                 parent_id = %s, revision = %s, status = 'draft', run_at = now(),
                 selector_id = (SELECT id FROM media_video.selectors WHERE name = %s),
                 style_id = (SELECT id FROM media_video.styles WHERE name = %s),
                 episode_id = %s, canvas = coalesce(%s, 'landscape'), fit = coalesce(%s, 'pad')
               WHERE id = %s""",
            (kind, project, title, logline, db.jsonb(params or {}), seed, parent_id, revision, selector, style,
             episode_id(episode, create=True, project=project) if episode else None, canvas, fit, timeline_id),
        )
        rows = []
        for index, clip in enumerate(clips, 1):
            if clip.end_s <= clip.start_s:
                raise EditError(f"clip {index} of {name!r} ends before it starts")
            rows.append({"idx": index, **{key: value if value is None or isinstance(value, (int, float)) else str(value)
                                          for key, value in asdict(clip).items()}})
        cur.execute(
            """INSERT INTO media_video.timeline_clips
                      (timeline_id, idx, v_asset_id, v_start_s, v_end_s, a_asset_id, a_start_s, a_end_s,
                       shot_id, segment_id, label, note)
               SELECT %s, idx, picture, start_s, end_s, sound, sound_start_s, sound_end_s,
                      shot_id, segment_id, label, note
                 FROM jsonb_to_recordset(%s) AS c(
                   idx integer, picture uuid, start_s double precision, end_s double precision,
                   sound uuid, sound_start_s double precision, sound_end_s double precision,
                   shot_id uuid, segment_id uuid, label text, note text)""",
            (timeline_id, db.jsonb(rows)),
        )
    return load(name)


def load(name: str) -> Timeline:
    cur = db.cursor()
    cur.execute(
        """SELECT t.id, t.name, t.kind, t.status, t.revision, t.project, t.title, t.seed,
                  parent.name, s.name, e.slug, t.canvas, t.fit, t.params, st.name
             FROM media_video.timelines t
             LEFT JOIN media_video.timelines parent ON parent.id = t.parent_id
             LEFT JOIN media_video.selectors s ON s.id = t.selector_id
             LEFT JOIN media_video.styles st ON st.id = t.style_id
             LEFT JOIN media.episodes e ON e.id = t.episode_id
            WHERE t.name = %s""",
        (name,),
    )
    if not (found := cur.fetchone()):
        raise EditError(f"no timeline is called {name!r}; `media timelines` lists them")
    (timeline_id, name, kind, status, revision, project, title, seed, after, selector, episode, canvas, fit, params,
     style) = found
    cur.execute(
        """SELECT v_asset_id, v_start_s, v_end_s, a_asset_id, a_start_s, a_end_s, shot_id, segment_id, label, note
             FROM media_video.timeline_clips WHERE timeline_id = %s ORDER BY idx""",
        (timeline_id,),
    )
    return Timeline(timeline_id, name, kind, status, revision, [Clip(*row) for row in cur.fetchall()], project,
                    title, seed, after, selector, episode, canvas, fit, params or {}, style)


def listing(project: str | None = None, episode: str | None = None) -> list[Summary]:
    """Every cut, or one project's or episode's, newest first."""
    cur = db.cursor()
    cur.execute(
        """SELECT t.name, t.kind, t.status, t.revision, count(c.id),
                  coalesce(sum(coalesce(c.a_end_s, c.v_end_s) - coalesce(c.a_start_s, c.v_start_s)), 0),
                  count(*) FILTER (WHERE n.verdict = 'drop'), count(*) FILTER (WHERE n.verdict = 'adjust'),
                  e.slug, st.name
             FROM media_video.timelines t
             LEFT JOIN media.episodes e ON e.id = t.episode_id
             LEFT JOIN media_video.styles st ON st.id = t.style_id
             LEFT JOIN media_video.timeline_clips c ON c.timeline_id = t.id
             LEFT JOIN LATERAL (SELECT verdict FROM media_video.clip_notes cn WHERE cn.clip_id = c.id
                                 ORDER BY cn.created_at DESC, cn.id DESC LIMIT 1) n ON true
            WHERE (%s::text IS NULL OR t.project = %s) AND (%s::text IS NULL OR e.slug = %s)
            GROUP BY t.id, e.slug, st.name
            ORDER BY t.run_at DESC, t.revision DESC, t.name""",
        (project, project, episode, episode),
    )
    return [Summary(name, kind, status, revision, clips, float(duration), dropped, adjusted, slug, style)
            for name, kind, status, revision, clips, duration, dropped, adjusted, slug, style in cur.fetchall()]


def mark(name: str, status: str) -> None:
    """Say where a cut stands: a draft, in review, approved or discarded."""
    if status not in STATUSES:
        raise EditError(f"a timeline is {', '.join(STATUSES[:-1])} or {STATUSES[-1]}, not {status!r}")
    cur = db.cursor()
    cur.execute("UPDATE media_video.timelines SET status = %s WHERE name = %s RETURNING id", (status, name))
    if not cur.fetchone():
        raise EditError(f"no timeline is called {name!r}; `media timelines` lists them")


def react(
    name: str, index: int, verdict: str, *, shift_s: float | None = None, extend_s: float | None = None,
    note: str | None = None, actor: str | None = None,
) -> Clip:
    """Record what someone made of one clip, counted from 1. Returns the clip.

    `adjust` says the choice was right and its edges were not: `shift_s` moves
    the clip later, `extend_s` lets it run longer, and either may be negative.
    """
    if verdict not in VERDICTS:
        raise EditError(f"a clip is told {', '.join(VERDICTS[:-1])} or {VERDICTS[-1]}, not {verdict!r}")
    if verdict != "adjust" and (shift_s or extend_s):
        raise EditError("only `adjust` moves a clip")
    timeline = load(name)
    if not 1 <= index <= len(timeline.clips):
        raise EditError(f"{name!r} has clips 1 to {len(timeline.clips)}, and no clip {index}")
    cur = db.cursor()
    cur.execute(
        # The clock, not the transaction's start: a second reaction has to come after the first.
        """INSERT INTO media_video.clip_notes (clip_id, verdict, shift_s, extend_s, note, actor, created_at)
           SELECT c.id, %s, %s, %s, %s, coalesce(%s, current_user), clock_timestamp()
             FROM media_video.timeline_clips c WHERE c.timeline_id = %s AND c.idx = %s""",
        (verdict, shift_s, extend_s, note, actor, timeline.id, index),
    )
    return timeline.clips[index - 1]


def reactions(name: str) -> dict[int, Reaction]:
    """The last thing said about each clip of a cut, by the clip's number."""
    cur = db.cursor()
    cur.execute(
        """SELECT DISTINCT ON (c.idx) c.idx, n.verdict, n.shift_s, n.extend_s, n.note
             FROM media_video.timelines t
             JOIN media_video.timeline_clips c ON c.timeline_id = t.id
             JOIN media_video.clip_notes n ON n.clip_id = c.id
            WHERE t.name = %s
            ORDER BY c.idx, n.created_at DESC, n.id DESC""",
        (name,),
    )
    return {index: Reaction(verdict, float(shift or 0.0), float(extend or 0.0), note)
            for index, verdict, shift, extend, note in cur.fetchall()}


def feedback(name: str) -> Feedback:
    """What a cut made after `name` should take from it and from the cuts before it."""
    timeline = load(name)
    said = reactions(name)
    held = []
    for index, clip in enumerate(timeline.clips, 1):
        reaction = said.get(index)
        if reaction is None or reaction.verdict == "drop":
            continue
        apart = clip.sound is not None and clip.sound_start_s is not None and clip.sound_end_s is not None
        # No recording is read from before it starts, and the sound moves with its picture.
        shift = max(reaction.shift_s, -clip.start_s, -clip.sound_start_s if apart else -clip.start_s)
        start = clip.start_s + shift
        end = max(clip.end_s + shift + reaction.extend_s, start + 0.1)
        moved = replace(clip, start_s=start, end_s=end)
        if apart:
            moved = replace(moved, sound_start_s=clip.sound_start_s + shift,
                            sound_end_s=clip.sound_start_s + shift + (end - start))
        held.append((index - 1, moved))
    cur = db.cursor()
    cur.execute(
        """WITH RECURSIVE line AS (
             SELECT id, parent_id FROM media_video.timelines WHERE name = %s
             UNION
             SELECT t.id, t.parent_id FROM media_video.timelines t JOIN line ON t.id = line.parent_id
           )
           SELECT DISTINCT c.shot_id, c.v_asset_id, c.v_start_s, c.v_end_s
             FROM line
             JOIN media_video.timeline_clips c ON c.timeline_id = line.id
            CROSS JOIN LATERAL (SELECT verdict FROM media_video.clip_notes n WHERE n.clip_id = c.id
                                 ORDER BY n.created_at DESC, n.id DESC LIMIT 1) last
            WHERE last.verdict = 'drop'
            ORDER BY c.v_asset_id, c.v_start_s""",
        (name,),
    )
    dropped = cur.fetchall()
    return Feedback(frozenset(str(shot_id) for shot_id, *_ in dropped if shot_id is not None), tuple(held),
                    tuple((str(asset_id), float(start), float(end)) for _, asset_id, start, end in dropped))
