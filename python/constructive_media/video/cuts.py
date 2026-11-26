"""Where one shot ends and the next begins.

A cut is a frame that differs sharply from the one before it. The difference
is the mean change in hue, saturation and brightness, averaged. Hue is counted
from 0 to 180 and the other two from 0 to 255: the scales content-aware scene
detectors conventionally use, and the ones the default threshold is tuned to.

Hue therefore weighs least. A cut between two pictures alike in brightness and
saturation scores low, and footage like that wants a lower threshold.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from .. import ffmpeg

if TYPE_CHECKING:
    import numpy as np

#: How different two neighbouring frames must be to call it a cut.
DEFAULT_THRESHOLD = 27.0
#: The picture must hold still this long for the next sharp change to be a cut.
MIN_SHOT_S = 0.5
ANALYSIS_WIDTH = 256
#: Frames scored per task, and the threads scoring them.
CHUNK = 16
WORKERS = max(1, min(4, os.cpu_count() or 1))


def batched(items: Iterable[np.ndarray], size: int) -> Iterator[list[np.ndarray]]:
    """`items` in lists of `size`; the last may be shorter."""
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


def scores(frames: Iterable[np.ndarray]) -> Iterator[float]:
    """How much each RGB frame differs from the one before it. The first is 0.

    Frames are scored a handful at a time on a few threads. numpy lets go of
    the interpreter while it works, so the threads do run side by side, and
    the scores still come out in order.
    """
    pending: deque[Future[list[float]]] = deque()
    before = None
    with ThreadPoolExecutor(WORKERS) as pool:
        for chunk in batched(frames, CHUNK):
            pending.append(pool.submit(_scores, before, chunk))
            before = chunk[-1]
            if len(pending) >= WORKERS * 2:
                yield from pending.popleft().result()
        while pending:
            yield from pending.popleft().result()


def _scores(before: np.ndarray | None, chunk: Sequence[np.ndarray]) -> list[float]:
    """The score of each frame in `chunk`, the first measured against `before`."""
    import numpy as np

    from . import pixels

    def planes(frame: np.ndarray) -> tuple[np.ndarray, ...]:
        hue, saturation, value = pixels.hsv(frame)
        # Hue as 0 to 180, the scale the threshold is tuned to.
        return hue / 2.0, saturation * 255.0, value * 255.0

    found: list[float] = []
    previous = planes(before) if before is not None else None
    for frame in chunk:
        current = planes(frame)
        if previous is None:
            found.append(0.0)
        else:
            found.append(float(sum(np.abs(now - then).mean() for now, then in zip(current, previous))) / 3.0)
        previous = current
    return found


def cuts(
    frames: Iterable[np.ndarray], *, threshold: float = DEFAULT_THRESHOLD, min_frames: int = 1,
) -> tuple[list[int], int]:
    """The index of the first frame of every new shot, and the frame count.

    A sharp change after `min_frames` of calm is a cut. Sharp changes that
    keep coming, as in a flash, a strobe or a slow dissolve, are one stretch
    of turbulence: it does not cut on every frame, and if it lasts long enough
    to be a shot of its own the next shot starts where it ends.
    """
    found: list[int] = []
    last_sharp = 0
    turbulence_from: int | None = None
    count = 0
    for index, score in enumerate(scores(frames)):
        count += 1
        calm = index - last_sharp >= min_frames
        if score >= threshold:
            if turbulence_from is None:
                if calm:
                    found.append(index)
                else:
                    turbulence_from = index
            last_sharp = index
        elif turbulence_from is not None and calm:
            if last_sharp - turbulence_from >= min_frames:
                found.append(last_sharp)
            turbulence_from = None
    return found, count


def shots(
    path: str | Path, *, threshold: float = DEFAULT_THRESHOLD, min_shot_s: float = MIN_SHOT_S,
) -> list[tuple[float, float]]:
    """The shots in a video as `(start_s, end_s)`. Empty if it cannot be read."""
    from . import pixels

    described = ffmpeg.probe(path)
    if not described or described.medium != "video" or not described.width or not described.height:
        return []
    fps = described.fps if described.fps and 1.0 <= described.fps <= 120.0 else 25.0
    width = min(ANALYSIS_WIDTH, described.width)
    height = max(2, round(width * described.height / described.width / 2) * 2)
    stream = (pixels.image(raw, width, height)
              for raw in ffmpeg.frames(path, fps=fps, size=(width, height)))
    starts, count = cuts(stream, threshold=threshold, min_frames=max(1, round(min_shot_s * fps)))
    if not count:
        return []
    edges = [0, *starts, count]
    return [(a / fps, b / fps) for a, b in zip(edges, edges[1:])]
