"""Whether the lit part of a picture moves with the rest of it.

Motion is not the same as something happening. A shot can be full of it, a
whip pan, a busy crowd, lights cycling by themselves, and show nothing worth
keeping. What makes a shot of a performance work is that two things move
together: a hand and the light it is steering, a dancer and the screen behind
them. Lights sweeping past someone standing still are a light show; someone
waving at lights that do not answer is someone waving. The moment one can be
seen causing the other is the one to keep.

So the picture is split in two and the movement of each is followed:

- the lit part: pixels both bright and strongly coloured, as beams, screens
  and stage lights are, and as people and floors in a dark room are not;
- everything else.

No model is needed for that, and it is done on a small picture ten times a
second: this is about large regions moving, not about detail.

Then the two movements are set against each other. The cause comes first, so
the best match within a little over half a second either way is kept, and the
lag is recorded too: above nothing, the scene moves and the lights follow.

A whole long shot is not scored at once. A take holds drawing, pausing,
walking and talking, and one figure across all of it falls merely because the
shot is long. An editor uses the good part, so the best four seconds are
scored, and where they are is recorded. That leans the other way, since a
longer shot has more windows to be best among; it is left as it is, because a
longer shot really is likelier to hold a good four seconds, and a shot's
length is something a ranking can already see.

The figure is the best of many tries: several windows, and a dozen lags in
each. Two movements that have nothing to do with each other still reach
about a half that way, so it is the top of the scale that means something. A
recipe weighs it beside other scores and never alone.

What this cannot do is tell a person from a curtain. Anything moving in time
with the lights scores. Whether someone is there is found by `detect`, and
putting the two together is a recipe's work.

Needs numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db, ffmpeg

#: Pictures a second.
FPS = 10
#: The size they are read at.
SIZE = (192, 108)
#: The length of a stretch worth using, in seconds, and how far apart two are tried.
WINDOW_S = 4.0
HOP_S = 1.0
#: How far one movement may lead the other, in seconds.
MAX_LAG_S = 0.6
#: How strongly coloured and how bright a pixel has to be to count as lit, 0 to 1.
SATURATED = 0.40
BRIGHT = 0.30
#: A shot has something lit in it when this many pixels are, in this many pictures.
_LIT_PIXELS = 20
_LIT_FRAMES = 5
#: Fewer pictures than this are under a second: nothing to follow.
_FEWEST = 6


@dataclass(frozen=True)
class Together:
    """How one shot's lit part moved with the rest. `corr` is None with nothing lit, or nothing moving."""

    n_frames: int
    lit_pixels: float
    corr: float | None = None
    lag_s: float | None = None
    best_at_s: float | None = None
    lit_motion: float | None = None
    scene_motion: float | None = None


def _best(scene: Any, lit: Any, reach: int) -> tuple[float, int]:
    """The closest the two come, and the lag it is at: above nothing, the scene leads."""
    import numpy as np

    best, at = -2.0, 0
    for lag in range(-reach, reach + 1):
        first, second = (scene[:len(scene) - lag], lit[lag:]) if lag >= 0 else (scene[-lag:], lit[:len(lit) + lag])
        if len(first) < 5 or first.std() < 1e-9 or second.std() < 1e-9:
            continue
        close = float(np.corrcoef(first, second)[0, 1])
        if close > best:
            best, at = close, lag
    return best, at


def together(frames: Any, fps: float = FPS, *, window_s: float = WINDOW_S, max_lag_s: float = MAX_LAG_S,
             ) -> Together | None:
    """Measure a stack of RGB pictures, `(n, height, width, 3)` from 0 to 1. None when there are too few."""
    import numpy as np

    frames = np.asarray(frames, dtype=np.float32)
    if len(frames) < _FEWEST:
        return None
    most, least = frames.max(axis=3), frames.min(axis=3)
    coloured = np.where(most > 1e-6, (most - least) / np.maximum(most, 1e-6), 0.0)
    lit = ((coloured >= SATURATED) & (most >= BRIGHT))[1:]
    change = np.abs(np.diff(most, axis=0))
    lit_pixels, other_pixels = lit.sum(axis=(1, 2)), (~lit).sum(axis=(1, 2))
    seen = Together(n_frames=len(frames), lit_pixels=float(lit_pixels.mean()))
    if (lit_pixels > _LIT_PIXELS).sum() < _LIT_FRAMES:
        return seen
    lit_motion = (change * lit).sum(axis=(1, 2)) / np.maximum(lit_pixels, 1)
    scene_motion = (change * ~lit).sum(axis=(1, 2)) / np.maximum(other_pixels, 1)
    if lit_motion.std() < 1e-9 or scene_motion.std() < 1e-9:
        return seen
    lights = (lit_motion - lit_motion.mean()) / lit_motion.std()
    scene = (scene_motion - scene_motion.mean()) / scene_motion.std()

    reach, window = int(max_lag_s * fps), int(window_s * fps)
    if len(lights) <= window:
        (best, lag), at = _best(scene, lights, reach), 0.0
    else:
        best, lag, at = -2.0, 0, 0.0
        for start in range(0, len(lights) - window + 1, max(1, int(HOP_S * fps))):
            close, its_lag = _best(scene[start:start + window], lights[start:start + window], reach)
            if close > best:
                best, lag, at = close, its_lag, start / fps
    if best < -1.0:
        return seen
    return Together(
        n_frames=len(frames), lit_pixels=float(lit_pixels.mean()), corr=best, lag_s=lag / fps, best_at_s=at,
        lit_motion=float(lit_motion.mean()), scene_motion=float(scene_motion.mean()),
    )


def watch(path: str | Path, start_s: float, seconds: float) -> Together | None:
    """Measure one stretch of a file."""
    import numpy as np

    width, height = SIZE
    pictures = [np.frombuffer(picture, dtype=np.uint8).reshape(height, width, 3)
                for picture in ffmpeg.frames(path, fps=FPS, size=SIZE, start_s=start_s, seconds=seconds)]
    if len(pictures) < _FEWEST:
        return None
    return together(np.stack(pictures).astype(np.float32) / 255.0)


def measure(match: str | None = None, *, redo: bool = False, limit: int | None = None) -> list[tuple[str, int, int]]:
    """Measure every shot that has not been. Returns (file name, shots measured, shots with something lit)."""
    cur = db.cursor()
    cur.execute(
        """SELECT sh.id, a.path, coalesce(a.proxy_path, a.path), sh.start_s, sh.duration_s
             FROM media_video.shots sh
             JOIN media.assets a ON a.id = sh.asset_id
            WHERE NOT coalesce(sh.is_black, false) AND sh.duration_s >= 1.0
              AND (%s = '' OR a.path ~* %s)
              AND (%s OR NOT EXISTS (SELECT 1 FROM media_video.coordination c WHERE c.shot_id = sh.id))
            ORDER BY a.ingested_at, a.path, sh.start_s
            LIMIT %s""",
        (match or "", match or "", redo, limit),
    )
    counts: dict[str, list[int]] = {}
    for shot_id, path, source, start, seconds in cur.fetchall():
        if not Path(source).exists():
            continue
        seen = watch(source, float(start), float(seconds))
        if seen is None:
            continue
        cur.execute(
            """INSERT INTO media_video.coordination
                 (shot_id, corr, lag_s, best_at_s, beam_motion, scene_motion, beam_pixels, n_frames)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (shot_id) DO UPDATE SET
                 corr = EXCLUDED.corr, lag_s = EXCLUDED.lag_s, best_at_s = EXCLUDED.best_at_s,
                 beam_motion = EXCLUDED.beam_motion, scene_motion = EXCLUDED.scene_motion,
                 beam_pixels = EXCLUDED.beam_pixels, n_frames = EXCLUDED.n_frames, computed_at = now()""",
            (shot_id, _rounded(seen.corr, 4), _rounded(seen.lag_s, 2), _rounded(seen.best_at_s, 2),
             _rounded(seen.lit_motion, 5), _rounded(seen.scene_motion, 5), round(seen.lit_pixels, 1), seen.n_frames),
        )
        count = counts.setdefault(Path(path).name, [0, 0])
        count[0] += 1
        count[1] += seen.corr is not None
    return [(name, measured, lit) for name, (measured, lit) in counts.items()]


def _rounded(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)
