"""Sound as numbers: any file ffmpeg can decode in, WAV files out.

Samples are 32-bit floats from -1 to 1, one row a moment: `(n,)` for one
channel, `(n, 2)` for two. Decoding and resampling are ffmpeg's, run as a
separate program; writing is done here and needs nothing but numpy.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from .. import ffmpeg

#: How ffmpeg is asked to resample. A tone at 96% of the band of a 24 kHz
#: recording, brought up to 48 kHz with ffmpeg's default filter, comes out with
#: an image of itself only 10 dB under it. With this filter the image is 67 dB
#: under, and the run takes no longer.
RESAMPLER = "filter_size=128"

#: The sample formats a WAV file is written in: bits, and whether they are floats.
FORMATS = {16: (1, 2), 24: (1, 3), 32: (3, 4)}

_MOST = 0xFFFFFFFF - 36


def shape(path: str | Path) -> tuple[int, int]:
    """How many channels the first sound stream of a file has, and its rate."""
    done = ffmpeg.run(ffmpeg.FFPROBE, "-v", "error", "-select_streams", "a:0",
                      "-show_entries", "stream=channels,sample_rate", "-of", "json", path)
    try:
        stream = json.loads(done.stdout)["streams"][0]
        return int(stream["channels"]), int(stream["sample_rate"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise ffmpeg.FfmpegError(f"{path} holds no sound") from None


def channels(path: str | Path) -> int:
    return shape(path)[0]


def rate(path: str | Path) -> int:
    return shape(path)[1]


def read(path: str | Path, *, rate: int | None = None, stereo: bool = True) -> Any:
    """The sound of a file, as two channels or as one, at `rate` or at its own.

    One channel read as two is the same on both sides, as loud as it was. Two
    read as one are averaged. More than two are folded to two by ffmpeg first.
    """
    import numpy as np

    wide = channels(path) > 1
    resample = ["-af", f"aresample={rate}:{RESAMPLER}"] if rate else []
    done = ffmpeg.run(
        ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-map", "0:a:0",
        *resample, "-f", "f32le", "-ac", "2" if wide else "1", "-", binary=True,
    )
    samples = np.frombuffer(done.stdout, dtype="<f4").astype(np.float32)
    if not wide:
        return np.stack([samples, samples], axis=1) if stereo else samples
    pairs = samples.reshape(-1, 2)
    return pairs if stereo else pairs.mean(axis=1)


def write(path: str | Path, samples: Any, rate: int, *, bits: int = 24) -> Path:
    """Write samples as a WAV file of 16 or 24 bit whole numbers, or 32 bit floats.

    Whole numbers cannot hold a sample past full scale, so those are held at
    it. Floats are written as they are.
    """
    import numpy as np

    if bits not in FORMATS:
        raise ValueError(f"a WAV file is written in {', '.join(map(str, FORMATS))} bits, not {bits}")
    tag, width = FORMATS[bits]
    samples = np.asarray(samples, dtype=np.float32)
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    if tag == 3:
        data = samples.astype("<f4").tobytes()
    else:
        top = (1 << (bits - 1)) - 1
        # In rows, whatever order the samples were handed over in: a table turned on its side is still a table.
        whole = np.rint(np.clip(samples, -1.0, 1.0) * top).astype("<i4", order="C")
        data = whole.astype("<i2").tobytes() if width == 2 else whole.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    if len(data) > _MOST:
        raise ValueError("the sound is too long for one WAV file")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, tag, channels,
                         rate, rate * channels * width, channels * width, bits, b"data", len(data))
    with open(path, "wb") as file:
        file.write(header)
        file.write(data)
    return path


def seconds(path: str | Path) -> float | None:
    """How long a file's sound runs, or None when it cannot be read as sound."""
    try:
        found = ffmpeg.probe(path)
    except ffmpeg.FfmpegError:
        return None
    return found.duration_s if found and found.has_audio else None
