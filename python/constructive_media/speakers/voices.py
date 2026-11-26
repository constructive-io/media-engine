"""Find the voices in a recording, and say which spoke each line.

Speech is cut into windows of a second and a half, laid only where the
transcript says someone was speaking. Each window becomes a row of numbers,
rows that sound like one person are grouped, and each group is a voice. A
segment of the transcript then belongs to the voice most of its windows do.

A remark too short for a window ("yes", "right") is not used to find the
voices. Once they are found it is given to the one it sounds most like, when
it sounds clearly more like that one than the next. Pieces cut from the middle
of longer speech went to the right voice that way 98 times in 100 at 0.4
seconds and more than 99 at 0.8, and a real "yes" is harder than those. What
is not clear is left unknown and not guessed.

How many voices there are is read from the speech unless it is given. Give it
when you know the cast: the count is then exact, and people who say little are
folded into the rest, which is sometimes what is wanted.

Going over a recording again keeps the names its voices were given.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

from .. import db, ffmpeg
from ..settings import Settings
from . import RATE, SHORTEST_S, Embedder, cluster, embedder as default_embedder

WINDOW_S = 1.5
HOP_S = 0.75
#: How much more a short remark must sound like one voice than the next.
MARGIN = 0.1
#: A voice found again in the same recording is this alike to what it was.
#: Two people on one poor microphone measured up to 0.64.
SAME = 0.75
#: A voice in another recording this alike is worth suggesting. One person on
#: two microphones measured 0.55 and up; two people, 0.47 at the most.
LIKE = 0.52
#: How a voice found here is marked in the database.
METHOD = "voice-embedding"
#: The windows a voice is best heard in, kept for its card.
BEST = 6
#: Seconds between those, so a card is not one sentence six times.
APART_S = 20.0
#: How many pieces of sound are turned into numbers at a time.
BATCH = 256


@dataclass(frozen=True)
class Voice:
    """One voice found in one recording."""

    label: str
    #: The middle of the voice, at unit length.
    centre: tuple[float, ...]
    windows: int
    segments: int
    speech_s: float
    #: How alike its windows are, 0 to 1. Low suggests more than one person.
    cohesion: float
    #: When, in the recording, it is most itself: the starts of a few windows.
    best: tuple[float, ...] = ()
    #: Whose it is, when a name was carried over from an earlier run.
    persona: str | None = None
    #: Whose it sounds like, from voices named in other recordings.
    like: str | None = None


@dataclass(frozen=True)
class Telling:
    """What was made of one recording's speech."""

    voices: list[Voice] = field(default_factory=list)
    #: For each segment, which voice (an index into `voices`) or None.
    spoken_by: list[int | None] = field(default_factory=list)
    #: Segments in which more than one voice was heard.
    split: int = 0

    @property
    def unknown(self) -> int:
        return sum(voice is None for voice in self.spoken_by)


@dataclass(frozen=True)
class Found:
    """One recording, gone over."""

    asset_id: Any
    name: str
    source: str
    model: str
    telling: Telling


def lay(segments: Sequence[tuple[float, float]], window_s: float = WINDOW_S,
        hop_s: float = HOP_S) -> tuple[list[tuple[int, float, float]], list[tuple[int, float, float]]]:
    """Where to listen: `(segment, start, end)` windows, and the short remarks.

    A segment as long as a window is covered by windows a hop apart. A shorter
    one, if it is long enough to tell a voice from at all, is a remark.
    """
    windows, remarks = [], []
    for index, (start, end) in enumerate(segments):
        if end - start >= window_s:
            at = start
            while at + window_s <= end + 1e-9:
                windows.append((index, at, at + window_s))
                at += hop_s
        elif end - start >= SHORTEST_S:
            remarks.append((index, start, end))
    return windows, remarks


def cuts(path: str | Path, spans: Sequence[tuple[float, float]]) -> Iterator[tuple[int, Any]]:
    """The sound of each `(start, end)` span of a file, as `(which span, samples)`.

    The file is read through once and never held whole, so a day of recording
    costs no more memory than a minute. A span that runs past the end of the
    sound comes back short.
    """
    import numpy as np

    wanted = sorted((round(start * RATE), round(end * RATE), index)
                    for index, (start, end) in enumerate(spans))
    held, first, position = bytearray(), 0, 0
    for piece in chain(ffmpeg.samples(path, rate=RATE), [None]):
        if position == len(wanted):
            break
        if piece is not None:
            held += piece
        have = first + len(held) // 2
        while position < len(wanted) and (piece is None or wanted[position][1] <= have):
            begin, end, index = wanted[position]
            sound = bytes(held[max(0, begin - first) * 2:max(0, min(end, have) - first) * 2])
            yield index, np.frombuffer(sound, dtype="<i2").astype(np.float32) / 32768.0
            position += 1
        if position < len(wanted):
            # Nothing before the next span is wanted again.
            spent = min(wanted[position][0], have) - first
            if spent > 0:
                del held[:spent * 2]
                first += spent


def tell(
    segments: Sequence[tuple[float, float]],
    sounds: Iterable[tuple[int, Any]],
    spans: tuple[list[tuple[int, float, float]], list[tuple[int, float, float]]],
    embedder: Embedder,
    *,
    voices: int | None = None,
    alike: float = cluster.ALIKE,
    least: int = cluster.LEAST,
) -> Telling:
    """Find the voices in speech already laid out and cut.

    `spans` is what `lay` made of `segments`, and `sounds` gives the sound of
    each span, windows first and then remarks, numbered in that order.
    """
    import numpy as np

    windows, remarks = spans
    rows = _embedded(sounds, len(windows) + len(remarks), embedder)
    # A window cut short by the end of the file is judged as a remark is.
    whole = [n for n in range(len(windows)) if rows[n] is not None and rows[n][1] >= 0.9 * WINDOW_S]
    if not whole:
        return Telling(spoken_by=[None] * len(segments))
    points = np.stack([rows[n][0] for n in whole]).astype(np.float64)
    if voices is None:
        labels = cluster.voices(points, alike=alike, least=least)
    else:
        labels = cluster.cut(cluster.ward(points), len(points), voices)
    count = max(labels) + 1
    members = [[n for n, label in enumerate(labels) if label == voice] for voice in range(count)]
    centres = np.stack([points[group].sum(axis=0) for group in members])
    centres /= np.maximum(np.linalg.norm(centres, axis=1, keepdims=True), 1e-12)

    heard: dict[int, Counter[int]] = {}
    for n, label in zip(whole, labels):
        heard.setdefault(windows[n][0], Counter())[label] += 1
    rest = sorted(set(range(len(windows))) - set(whole))
    late = [(windows[n][0], rows[n]) for n in rest] + \
           [(segment, rows[len(windows) + n]) for n, (segment, _, _) in enumerate(remarks)]
    for segment, row in late:
        if row is None or segment in heard:
            continue
        nearest = _nearest(centres @ row[0])
        if nearest is not None:
            heard[segment] = Counter({nearest: 1})

    spoken_by: list[int | None] = [None] * len(segments)
    for segment, votes in heard.items():
        # Most windows wins; level, the voice that speaks more does.
        spoken_by[segment] = min(votes, key=lambda voice: (-votes[voice], voice))
    found = []
    for voice, group in enumerate(members):
        mine = [index for index, speaker in enumerate(spoken_by) if speaker == voice]
        close = points[group] @ centres[voice]
        found.append(Voice(
            label=f"voice{voice + 1}",
            centre=tuple(float(value) for value in centres[voice]),
            windows=len(group),
            segments=len(mine),
            speech_s=round(sum(segments[index][1] - segments[index][0] for index in mine), 3),
            cohesion=round(float(close.mean()), 3),
            best=_best([windows[whole[n]][1] for n in group], close),
        ))
    return Telling(found, spoken_by, sum(len(votes) > 1 for votes in heard.values()))


def _embedded(sounds: Iterable[tuple[int, Any]], count: int, embedder: Embedder) -> list[Any]:
    """For each span, its row of numbers and how long its sound was, or None."""
    rows: list[Any] = [None] * count
    waiting: list[tuple[int, Any]] = []

    def flush() -> None:
        if waiting:
            answers = embedder.embed([sound for _, sound in waiting])
            for (index, sound), row in zip(waiting, answers):
                rows[index] = (row, len(sound) / RATE)
            waiting.clear()

    for index, sound in sounds:
        if len(sound) >= SHORTEST_S * RATE:
            waiting.append((index, sound))
            if len(waiting) >= BATCH:
                flush()
    flush()
    return rows


def _nearest(alike: Any) -> int | None:
    """The voice a short piece of sound is, if it is clearly one of them."""
    order = sorted(range(len(alike)), key=lambda voice: -alike[voice])
    if len(order) > 1 and alike[order[0]] - alike[order[1]] < MARGIN:
        return None
    return order[0]


def _best(starts: Sequence[float], close: Any) -> tuple[float, ...]:
    """A few windows nearest the centre, far enough apart to be different moments."""
    chosen: list[float] = []
    for apart in (APART_S, WINDOW_S):
        for n in sorted(range(len(starts)), key=lambda n: -close[n]):
            if len(chosen) == BEST:
                break
            if all(abs(starts[n] - start) >= apart for start in chosen):
                chosen.append(starts[n])
    return tuple(sorted(chosen))


def find(
    match: str | None = None,
    *,
    voices: int | None = None,
    alike: float = cluster.ALIKE,
    least: int = cluster.LEAST,
    force: bool = False,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
) -> list[Found]:
    """Find the voices in each transcribed recording that has not been gone over.

    With `force`, recordings are gone over again. A voice that comes out where
    a named one was keeps the name.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path, coalesce(a.proxy_path, a.path)
             FROM media.assets a
            WHERE (%s = '' OR a.path ~* %s)
              AND EXISTS (SELECT 1 FROM media_video.segments s WHERE s.asset_id = a.id)
              AND (%s OR NOT EXISTS (SELECT 1 FROM media_video.speaker_clusters c WHERE c.asset_id = a.id))
            ORDER BY a.ingested_at, a.path""",
        (match or "", match or "", force),
    )
    waiting = cur.fetchall()
    if waiting and embedder is None:
        embedder = default_embedder(settings=settings)
    done = []
    for asset_id, path, source in waiting:
        cur.execute(
            "SELECT id, start_s, end_s FROM media_video.segments WHERE asset_id = %s ORDER BY idx",
            (asset_id,),
        )
        rows = cur.fetchall()
        segments = [(float(start), float(end)) for _, start, end in rows]
        spans = lay(segments)
        sounds = cuts(source, [(start, end) for _, start, end in spans[0] + spans[1]])
        telling = tell(segments, sounds, spans, embedder, voices=voices, alike=alike, least=least)
        with db.transaction():
            telling = _write(asset_id, [row[0] for row in rows], telling, embedder.model)
        done.append(Found(asset_id, Path(path).name, source, embedder.model, telling))
    return done


def named(model: str, *, asset_id: Any = None, elsewhere: bool = False) -> list[tuple[str, Any, Any]]:
    """Voices that have a name, as `(name, person, centre)`, in one model's numbers.

    Those of one recording, or with `elsewhere` those of every other.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT p.name, p.id, c.centroid
             FROM media_video.speaker_clusters c
             JOIN media.personas p ON p.id = c.persona_id
            WHERE c.model = %s AND c.centroid IS NOT NULL
              AND CASE WHEN %s THEN c.asset_id <> %s::uuid ELSE c.asset_id = %s::uuid END
            ORDER BY c.created_at, c.label""",
        (model, elsewhere, str(asset_id), str(asset_id)),
    )
    return cur.fetchall()


def whose(centre: Sequence[float], known: Sequence[tuple[str, Any, Any]],
          least: float = LIKE) -> tuple[str, Any, float] | None:
    """The named voice a centre is most like, as `(name, person, how alike)`.

    None when it is not at least `least` alike to any of them.
    """
    best = None
    for name, person, other in known:
        if len(other) != len(centre):
            continue
        alike = sum(a * b for a, b in zip(centre, other))
        if alike >= least and (best is None or alike > best[2]):
            best = (name, person, alike)
    return best


def _write(asset_id: Any, segment_ids: Sequence[Any], telling: Telling, model: str) -> Telling:
    """Store a recording's voices in place of those it had. Returns them with names."""
    from dataclasses import replace

    before = named(model, asset_id=asset_id)
    others = named(model, asset_id=asset_id, elsewhere=True)
    cur = db.cursor()
    cur.execute("DELETE FROM media_video.speaker_clusters WHERE asset_id = %s", (asset_id,))
    voices, given = [], []
    for index, voice in enumerate(telling.voices):
        kept, like = whose(voice.centre, before, SAME), whose(voice.centre, others)
        cur.execute(
            """INSERT INTO media_video.speaker_clusters
                 (asset_id, label, persona_id, n_segments, method, model, centroid,
                  cohesion, n_windows, speech_s)
               VALUES (%s, %s, %s, %s, %s, %s, %s::real[], %s, %s, %s) RETURNING id""",
            (asset_id, voice.label, kept[1] if kept else None, voice.segments, METHOD, model,
             list(voice.centre), voice.cohesion, voice.windows, voice.speech_s),
        )
        (cluster_id,) = cur.fetchone()
        given += [{"segment": str(segment_ids[n]), "cluster": str(cluster_id)}
                  for n, speaker in enumerate(telling.spoken_by) if speaker == index]
        voices.append(replace(voice, persona=kept[0] if kept else None,
                              like=like[0] if like and not kept else None))
    cur.execute(
        """UPDATE media_video.segments s SET cluster_id = g.cluster
             FROM jsonb_to_recordset(%s) AS g(segment uuid, cluster uuid)
            WHERE s.id = g.segment""",
        (db.jsonb(given),),
    )
    return replace(telling, voices=voices)
