"""Object detection: what is in a frame, apart from what finds it.

A `Detector` is given image files and returns, for each, the things it found.
Boxes are normalised to the frame: centre, width and height, each from 0 to 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings


@dataclass(frozen=True)
class Detection:
    """One thing found in one frame."""

    label: str
    confidence: float
    cx: float
    cy: float
    w: float
    h: float

    @property
    def area(self) -> float:
        """The share of the frame the box covers."""
        return self.w * self.h

    @classmethod
    def from_corners(
        cls, label: str, confidence: float,
        x0: float, y0: float, x1: float, y1: float, width: float, height: float,
    ) -> Detection:
        """A detection from pixel corners in a frame of `width` by `height`."""
        x0, x1 = (min(max(x / width, 0.0), 1.0) for x in (x0, x1))
        y0, y1 = (min(max(y / height, 0.0), 1.0) for y in (y0, y1))
        return cls(label, float(confidence), (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)


@runtime_checkable
class Detector(Protocol):
    """Something that can find objects in images."""

    name: str
    #: What is recorded beside each detection, so results can be told apart.
    model: str

    def detect(self, images: Sequence[Path], confidence: float = 0.35) -> list[list[Detection]]:
        """The detections for each image, in the order the images were given."""
        ...


def detector(name: str | None = None, *, settings: Settings | None = None) -> Detector:
    """The chosen detector, or the configured default."""
    return adapters.get("detection", name, settings=settings)
