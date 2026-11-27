"""A denoiser that needs no model: the `ffmpeg` program's own.

ffmpeg's `afftdn` takes a steady noise off a recording by its spectrum. It
is told how loud the noise is and takes that much off every band. It is
quick and it is always there, and it is the lesser of the two: it mends a
steady hiss, and a model mends more and costs the voice less.
"""

from __future__ import annotations

from pathlib import Path

from .. import ffmpeg
from . import DenoiseError

#: How much of the noise is taken off, in dB. More than this and what is left begins to ring.
REDUCTION_DB = 12
#: What `afftdn` will take as a noise floor.
FLOOR_DB = (-80.0, -20.0)
DEFAULT_FLOOR_DB = -50.0


class FfmpegDenoiser:
    """Steady noise taken off by its spectrum, with ffmpeg's `afftdn`."""

    name = "ffmpeg"

    def filter(self, floor: float | None = None) -> str:
        low, high = FLOOR_DB
        told = min(high, max(low, DEFAULT_FLOOR_DB if floor is None else floor))
        return f"afftdn=nr={REDUCTION_DB}:nf={told:.1f}:tn=1"

    def clean(self, source: Path, target: Path, *, floor: float | None = None) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-vn",
                       "-af", self.filter(floor), "-ac", "1", "-c:a", "pcm_f32le", target)
        except ffmpeg.FfmpegError as error:
            raise DenoiseError(f"{source} could not be cleaned: {error}") from None
        return target
