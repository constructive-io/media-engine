"""Denoising: a voice kept and the noise under it taken off.

A `Denoiser` is given a recording of somebody speaking and writes it again
with less of what is not them: hiss, a fan, the room's own tone. The engine
asks for one by slot, `denoise`, and never knows which model did it.

It is the last thing to reach for. A recording is first measured, and what a
filter mends (rumble, mains hum) is mended with a filter, which cannot hurt
the voice. `constructive_media.audio.clean` does the measuring and decides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings


class DenoiseError(RuntimeError):
    """A recording could not be cleaned."""


@runtime_checkable
class Denoiser(Protocol):
    """Something that takes the noise off a voice."""

    name: str

    def clean(self, source: Path, target: Path, *, floor: float | None = None) -> Path:
        """Write `source`, with less noise, to `target` as a WAV file of one channel, and return it.

        `floor` is how loud the noise was measured to be, in dB under full
        scale, for a denoiser that wants telling. The file written may be at
        another rate than the source: whoever asked brings it back.
        """
        ...


def denoiser(name: str | None = None, *, settings: Settings | None = None) -> Denoiser:
    """The chosen denoiser, or the configured default."""
    return adapters.get("denoise", name, settings=settings)
