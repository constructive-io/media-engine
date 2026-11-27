"""How well each transcribed line can be heard: its level, the noise under it, and whether it is distorted.

A recogniser's confidence says how sure it was of the words, which is not the
same thing. It was trained on noisy sound and reads a muffled lapel clearly;
it is just as sure of a line nobody would want to listen to. So the sound is
measured for itself, a line at a time:

- `rms_db`: the level of the line;
- `noise_db`: the level of the recording when nobody speaks;
- `snr_db`: how far the speech stands above that;
- `clipped_ratio`: the share of the line at the very top of the scale;
- `hf_ratio`: the share of it above four kilohertz.

The noise is the quietest tenth of the whole recording, not of the line: a
line may have no silence in it at all, and the room does not change between
one line and the next. The speech is the loudest quarter of the line, not its
average, since a line is mostly the gaps between words and averaging them in
makes every voice several decibels quieter than it is.

Assembling a cut already reads this: where every recording of a moment has
been measured, the sound is taken from the cleanest.

Needs numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .. import db
from . import pcm

#: Samples a second the sound is read at.
RATE = 16000
#: Seconds each level is taken over.
STEP_S = 0.020
#: A sample this near the top of the scale was cut off there.
_CLIPPED = 0.99
#: Where "high" starts, in hertz. Hiss and sibilance live above it, and a
#: recording made through cloth has almost nothing there.
_HIGH_HZ = 4000


@dataclass(frozen=True)
class Heard:
    rms_db: float
    noise_db: float
    snr_db: float
    clipped_ratio: float
    hf_ratio: float


def _decibels(level: float) -> float:
    import math

    return 20.0 * math.log10(max(float(level), 1e-9))


def measure(samples: Any, spans: Sequence[tuple[float, float]], rate: int = RATE) -> list[Heard | None]:
    """Each span of one channel of sound, measured; None for a span too short or outside the sound."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.float64)
    size = int(rate * STEP_S)
    frames = samples[:len(samples) // size * size].reshape(-1, size)
    if len(frames) < 3:
        return [None for _ in spans]
    levels = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    noise_db = _decibels(float(np.percentile(levels, 10)))
    pitches = np.fft.rfftfreq(size, 1.0 / rate)
    window = np.hanning(size)

    heard: list[Heard | None] = []
    for start, end in spans:
        first, last = max(0, int(start / STEP_S)), min(len(levels), int(end / STEP_S))
        if last - first < 3:
            heard.append(None)
            continue
        spectrum = np.abs(np.fft.rfft(frames[first:last] * window, axis=1))
        high = spectrum[:, pitches >= _HIGH_HZ].sum(axis=1) / (spectrum.sum(axis=1) + 1e-12)
        heard.append(Heard(
            rms_db=_decibels(float(np.sqrt((levels[first:last] ** 2).mean()))),
            noise_db=noise_db,
            snr_db=_decibels(float(np.percentile(levels[first:last], 75))) - noise_db,
            clipped_ratio=float((np.abs(frames[first:last]) > _CLIPPED).mean()),
            hf_ratio=float(high.mean()),
        ))
    return heard


def scan(project: str | None = None, match: str | None = None, *, redo: bool = False) -> list[tuple[str, int]]:
    """Measure every transcribed line that has not been, or with `redo` every one. Returns (file name, lines)."""
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path FROM media.assets a
            WHERE (%s::text IS NULL OR a.project = %s) AND (%s = '' OR a.path ~* %s)
              AND EXISTS (SELECT 1 FROM media_video.segments s
                           WHERE s.asset_id = a.id
                             AND (%s OR NOT EXISTS (SELECT 1 FROM media_video.audio_quality q
                                                     WHERE q.segment_id = s.id)))
            ORDER BY a.duration_s, a.path""",
        (project, project, match or "", match or "", redo),
    )
    done = []
    for asset_id, path in cur.fetchall():
        if not Path(path).exists():
            continue
        cur.execute("SELECT id, start_s, end_s FROM media_video.segments WHERE asset_id = %s ORDER BY idx",
                    (asset_id,))
        lines = cur.fetchall()
        heard = measure(pcm.read(path, rate=RATE, stereo=False), [(float(s), float(e)) for _, s, e in lines])
        measured = [(segment_id, round(one.rms_db, 2), round(one.noise_db, 2), round(one.snr_db, 2),
                     round(one.clipped_ratio, 5), round(one.hf_ratio, 4))
                    for (segment_id, _, _), one in zip(lines, heard) if one]
        if measured:
            cur.executemany(
                """INSERT INTO media_video.audio_quality
                     (segment_id, rms_db, noise_db, snr_db, clipped_ratio, hf_ratio)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (segment_id) DO UPDATE SET
                     rms_db = EXCLUDED.rms_db, noise_db = EXCLUDED.noise_db, snr_db = EXCLUDED.snr_db,
                     clipped_ratio = EXCLUDED.clipped_ratio, hf_ratio = EXCLUDED.hf_ratio,
                     computed_at = now()""",
                measured,
            )
        done.append((Path(path).name, len(measured)))
    return done


def verdicts(project: str | None = None) -> list[tuple[str, int]]:
    """How many measured lines are clean, usable, noisy, buried or distorted, most first."""
    cur = db.cursor()
    cur.execute(
        """SELECT v.verdict, count(*) FROM media_video.v_audible v
             JOIN media.assets a ON a.id = v.asset_id
            WHERE v.snr_db IS NOT NULL AND (%s::text IS NULL OR a.project = %s)
            GROUP BY 1 ORDER BY 2 DESC, 1""",
        (project, project),
    )
    return [(verdict, int(count)) for verdict, count in cur.fetchall()]
