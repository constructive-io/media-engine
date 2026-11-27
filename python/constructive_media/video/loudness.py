"""How loud each shot is, and how far above what surrounds it.

The sound knows things the picture does not. A cheer, a gasp, a drop in the
music: these mark the moments an editor would keep, and no measure of the
pixels sees them. Sharpness says a shot can be used; loudness says something
happened in it.

Three numbers a shot:

- `loudness`: its level, in decibels below full scale;
- `peak_db`: its loudest instant;
- `lift_db`: how far it sits above the minute around it.

The lift is the one that matters. A concert is loud throughout, so a level
says almost nothing, but a shot six decibels above its own surroundings is a
reaction. Measuring against what is near, not against the whole file, also
survives a recording whose level drifts, as a hand-held one always does.

Needs numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db, ffmpeg

#: Samples a second the sound is read at. A level needs no more.
SAMPLE_RATE = 8000
#: Levels a second.
RATE = 100
#: How much of what surrounds a shot it is measured against, in seconds.
WINDOW_S = 60.0
#: The quietest a level is written as: 120 decibels under full scale.
_SILENT = 1e-6


@dataclass(frozen=True)
class Level:
    loudness: float
    peak_db: float
    lift_db: float


def decibels(level: float) -> float:
    import math

    return 20.0 * math.log10(max(float(level), _SILENT))


def levels(path: str | Path) -> tuple[Any, Any] | None:
    """The level and the peak of a file's sound, `RATE` of each a second, or None with no sound."""
    import numpy as np

    block = SAMPLE_RATE // RATE
    heard = np.frombuffer(b"".join(ffmpeg.samples(path, rate=SAMPLE_RATE)), dtype="<i2")
    whole = len(heard) // block * block
    if not whole:
        return None
    blocks = heard[:whole].astype(np.float32).reshape(-1, block) / 32768
    return np.sqrt((blocks.astype(np.float64) ** 2).mean(axis=1)), np.abs(blocks).max(axis=1)


def level(rms: Any, peak: Any, start_s: float, end_s: float, *, window_s: float = WINDOW_S) -> Level | None:
    """One stretch of a file against what surrounds it, or None when it is outside the sound.

    A stretch that is most of its file has no surroundings to speak of, and
    would be measured against itself: every such shot would have a lift of
    nothing. It is set against the whole file instead, which at least says
    whether the moment is loud for the recording.
    """
    import numpy as np

    first = max(0, int(start_s * RATE))
    last = min(len(rms), max(int(end_s * RATE), first + 1))
    if last <= first:
        return None
    if len(rms) > 2 * (last - first):
        reach = int(window_s * RATE / 2)
        around = rms[max(0, first - reach):last + reach]
    else:
        around = rms
    loudness = decibels(np.sqrt((rms[first:last] ** 2).mean()))
    return Level(loudness, decibels(peak[first:last].max()),
                 loudness - decibels(np.sqrt((around ** 2).mean())))


def measure(match: str | None = None, *, redo: bool = False, window_s: float = WINDOW_S) -> list[tuple[str, int]]:
    """Measure every shot that has not been, or with `redo` every shot. Returns (file name, shots measured)."""
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path, coalesce(a.proxy_path, a.path)
             FROM media.assets a
            WHERE a.has_audio AND (%s = '' OR a.path ~* %s)
              AND EXISTS (SELECT 1 FROM media_video.shots s
                           WHERE s.asset_id = a.id AND (%s OR s.loudness IS NULL))
            ORDER BY a.ingested_at, a.path""",
        (match or "", match or "", redo),
    )
    done = []
    for asset_id, path, source in cur.fetchall():
        heard = levels(source)
        if heard is None:
            continue
        cur.execute(
            """SELECT id, start_s, end_s FROM media_video.shots
                WHERE asset_id = %s AND (%s OR loudness IS NULL) ORDER BY idx""",
            (asset_id, redo),
        )
        found = [(shot_id, level(*heard, float(start), float(end), window_s=window_s))
                 for shot_id, start, end in cur.fetchall()]
        measured = [(one.loudness, one.peak_db, one.lift_db, shot_id) for shot_id, one in found if one]
        if measured:
            cur.executemany(
                "UPDATE media_video.shots SET loudness = %s, peak_db = %s, lift_db = %s WHERE id = %s", measured)
            done.append((Path(path).name, len(measured)))
    return done
