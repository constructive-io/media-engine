"""Sound as the picture a speaker model reads: log mel filterbank energies.

Speaker models published from the Kaldi lineage are all trained on one exact
recipe, and they are only as good as the recipe is exact: a different window
or a mel scale a few hertz off still gives numbers, just not the ones the
model learned from. This is that recipe in numpy, held to the reference
implementation by a test: 25 ms frames every 10 ms, the mean taken off each
frame, pre-emphasis, a Hamming window, the power spectrum, then mel bands from
20 Hz to half the sample rate, in logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

RATE = 16000
BANDS = 80
FRAME_S = 0.025
SHIFT_S = 0.010
PRE_EMPHASIS = 0.97
LOW_HZ = 20.0
#: The smallest energy a band is allowed, so silence has a logarithm.
FLOOR = 1.1920928955078125e-07


def frames_in(samples: int, rate: int = RATE) -> int:
    """How many frames a sound of `samples` gives."""
    frame, shift = round(rate * FRAME_S), round(rate * SHIFT_S)
    return 0 if samples < frame else 1 + (samples - frame) // shift


def fbank(sound: Any, rate: int = RATE, bands: int = BANDS) -> Any:
    """`frames x bands` log mel energies for a mono sound between -1 and 1."""
    import numpy as np

    frame, shift = round(rate * FRAME_S), round(rate * SHIFT_S)
    count = frames_in(len(sound), rate)
    if not count:
        return np.zeros((0, bands), dtype=np.float32)
    # The models were trained on 16-bit sample values, not on fractions.
    sound = np.asarray(sound, dtype=np.float32) * 32768.0
    framed = np.lib.stride_tricks.sliding_window_view(sound, frame)[::shift][:count].astype(np.float64)
    framed = framed - framed.mean(axis=1, keepdims=True)
    # Each sample less most of the one before; the first has only itself.
    before = np.concatenate([framed[:, :1], framed[:, :-1]], axis=1)
    framed = (framed - PRE_EMPHASIS * before) * _window(frame)
    size = 1 << (frame - 1).bit_length()
    power = np.abs(np.fft.rfft(framed, n=size, axis=1)) ** 2
    return np.log(np.maximum(power @ _mel(size, rate, bands).T, FLOOR)).astype(np.float32)


def levelled(features: Any) -> Any:
    """Features with each band's mean taken off: what the microphone and the
    room add to every frame alike, removed."""
    return features - features.mean(axis=0, keepdims=True) if len(features) else features


@lru_cache(maxsize=4)
def _window(frame: int) -> Any:
    import numpy as np

    return 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(frame) / (frame - 1))


@lru_cache(maxsize=4)
def _mel(size: int, rate: int, bands: int) -> Any:
    """`bands x (size/2 + 1)` triangles, evenly spaced on the mel scale."""
    import numpy as np

    def mel(hz: Any) -> Any:
        return 1127.0 * np.log(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)

    low, high = mel(LOW_HZ), mel(rate / 2)
    step = (high - low) / (bands + 1)
    left = low + step * np.arange(bands)[:, None]
    at = mel(rate / size * np.arange(size // 2))[None, :]
    rising, falling = (at - left) / step, (left + 2 * step - at) / step
    triangles = np.maximum(0.0, np.minimum(rising, falling))
    # The top bin, at exactly half the sample rate, belongs to no band.
    return np.pad(triangles, ((0, 0), (0, 1)))
