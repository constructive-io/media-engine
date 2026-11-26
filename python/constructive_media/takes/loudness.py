"""Place two recordings by how they sound, for when there are no words.

Two devices in one room do not record the same signal: the levels, the
placement and the noise all differ, and comparing waveforms breaks on every
one of those. What survives is *when the sound gets louder*: a clap, a
consonant, a chair. So each recording is reduced to the moments its loudness
rises, a hundred values a second, and the two are slid past each other at
every possible offset.

The measure of a match is how far its peak stands above every other offset.
A real match is a spike. Two unrelated recordings give a lumpy curve with no
winner, and saying so is the useful answer.

This is the second choice. Words place a recording more surely where there
are words (see `align`); this is for music, ambience and silence.

Needs numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ffmpeg

#: Loudness values a second.
RATE = 100
#: Samples a second the sound is read at. Loudness needs no more.
SAMPLE_RATE = 8000
#: How far the peak must stand out, in standard deviations, to be believed.
#: Measured on real recordings: true pairs scored 25 and up, unrelated pairs
#: 7 and below.
MIN_STANDOUT = 12.0
#: Offsets this close to the peak are part of it, not the background.
_PEAK_S = 5.0


@dataclass(frozen=True)
class Heard:
    """Where one recording sits against another, by sound."""

    #: Seconds to add to `other`'s time to reach `ref`'s time.
    offset_s: float
    #: How far the peak stands above the rest, in standard deviations.
    standout: float

    @property
    def sure(self) -> bool:
        return self.standout >= MIN_STANDOUT


def rises(path: str | Path) -> Any:
    """When a file's loudness rises, `RATE` values a second, or None.

    Loudness is taken on a logarithmic scale, as the ear takes it, so a quiet
    recording and a loud one of the same room have the same shape.
    """
    import numpy as np

    block = SAMPLE_RATE // RATE
    levels, left = [], b""
    for piece in ffmpeg.samples(path, rate=SAMPLE_RATE):
        piece = left + piece
        whole = len(piece) // (2 * block) * (2 * block)
        left = piece[whole:]
        if whole:
            heard = np.frombuffer(piece[:whole], dtype="<i2").astype(np.float32) / 32768
            levels.append(np.sqrt((heard.reshape(-1, block) ** 2).mean(axis=1)))
    if not levels:
        return None
    level = np.log10(np.concatenate(levels).astype(np.float64) + 1e-4)
    # Five values is a twentieth of a second: enough to steady the level
    # without blurring when it moved.
    # The ends are held, not padded with silence: a file beginning is not a
    # sound, and every file would otherwise open with the same loud one.
    steady = np.convolve(np.pad(level, 2, mode="edge"), np.full(5, 0.2), mode="valid")
    rising = np.maximum(np.diff(steady, prepend=steady[0]), 0)
    rising -= rising.mean()
    scale = rising.std()
    return rising / scale if scale > 1e-9 else None


def offset(ref: Any, other: Any, *, min_overlap_s: float = 20.0) -> Heard | None:
    """Slide `other` past `ref` and report where they agree best.

    Every offset at which the two overlap for `min_overlap_s` is tried, so
    either may be the longer and neither need contain the other.
    """
    import numpy as np

    if ref is None or other is None or len(ref) < 2 or len(other) < 2:
        return None
    size = 1 << (len(ref) + len(other) - 2).bit_length()
    product = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(other, size)), size)
    # At lag k, other[i] is compared with ref[i + k].
    lags = np.arange(-(len(other) - 1), len(ref))
    agreement = np.concatenate([product[-(len(other) - 1):], product[:len(ref)]])
    overlap = np.minimum(len(ref), lags + len(other)) - np.maximum(0, lags)
    usable = overlap >= min(min_overlap_s * RATE, min(len(ref), len(other)) / 2)
    # A sum over more values wanders further by chance alone. Dividing by the
    # root of the overlap puts every offset on the same footing.
    score = np.where(usable, agreement / np.sqrt(np.maximum(overlap, 1)), -np.inf)
    best = int(np.argmax(score))
    background = usable.copy()
    near = int(_PEAK_S * RATE)
    background[max(0, best - near):best + near] = False
    rest = score[background]
    if len(rest) < 10 or rest.std() < 1e-9:
        return None
    return Heard(float(lags[best]) / RATE, float((score[best] - rest.mean()) / rest.std()))


def measure(ref: str | Path, other: str | Path) -> Heard | None:
    """Place the file `other` against the file `ref` by sound."""
    return offset(rises(ref), rises(other))
