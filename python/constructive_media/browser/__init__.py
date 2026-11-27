"""A browser the engine can watch.

A page is the easiest way to draw a slide, a caption or a lower third, so the
engine films one. It asks for a browser by slot, `browser`, opens a page in
it, and keeps every picture the browser paints with the time it was painted.
It never knows which browser it was.

The built-in adapter runs a Chrome or a Chromium that is already installed,
with no window, as a separate program. Nothing of it is part of this package.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings


class BrowserError(RuntimeError):
    """There is no browser to run, or the one there is did not do what was asked."""


@dataclass(frozen=True)
class Frame:
    """One picture of the page, and when the browser painted it.

    `at` is in seconds on the browser's own clock, which is the clock a page
    reads with `Date.now()`.
    """

    at: float
    file: Path


@dataclass(frozen=True)
class Filmed:
    """What came of watching a page.

    `start` is the moment the page said its clock began, on the same clock as
    every frame's `at`. `notes` are the errors the page raised while it ran.
    `shown` is the moment the page had its first moment up, when it says: a
    frame from before it is of a page still getting ready.
    """

    start: float
    frames: list[Frame]
    notes: list[str] = field(default_factory=list)
    shown: float | None = None


@runtime_checkable
class Browser(Protocol):
    """Something that opens a page and can say what it looked like."""

    name: str

    def film(self, url: str, folder: Path, *, size: tuple[int, int], seconds: float,
             tell: Callable[[str], None] | None = None) -> Filmed:
        """Open `url` at `size` and keep every picture of it in `folder` until the page says it is done.

        The page says when it starts and when it is done by setting
        `window.__stage`: see docs/STAGE.md. `seconds` is how long it is
        expected to run, to know when to stop waiting for it.
        """
        ...

    def stills(self, url: str, moments: Sequence[tuple[float, Path]], *, size: tuple[int, int],
               tell: Callable[[str], None] | None = None) -> list[str]:
        """Open `url` at `size`, hold it at each moment in turn, and write a picture of each to the file beside it.

        The page is held by `window.__stage.at(seconds)`, which answers when
        it has been drawn there: see docs/STAGE.md. What comes back is the
        errors the page raised.
        """
        ...

    def print(self, url: str, target: Path) -> Path:
        """Write `url` to `target` as a PDF, laid out as the page's own print styles ask."""
        ...


def browser(name: str | None = None, *, settings: Settings | None = None) -> Browser:
    """The chosen browser, or the configured default."""
    return adapters.get("browser", name, settings=settings)
