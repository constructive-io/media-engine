"""Speech regions: a recording packed down to its speech, and the way back.

A recogniser given noise or silence writes something anyway, and one that
listens in fixed windows loses the words a window's edge lands on. So the
stretches where nobody speaks are cut out before it listens, and the speech
is handed over in pieces that each begin and end on a pause. Every time the
recogniser reports is then mapped back to the recording.

Speech is never cut where there is no pause: a recogniser handed a piece that
starts mid-sentence may write three words and stop.

Works on WAV files with the standard library alone.
"""

from __future__ import annotations

import wave
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Regions closer together than this are one region: a breath, not a break.
JOIN_WITHIN_S = 0.2
#: The silence put between two regions in the packed file.
PAUSE_S = 0.2
#: Kept on each side of a region, so a word's first and last sounds survive.
PAD_S = 0.05
#: The shortest file worth writing: recognisers refuse less than a second.
SHORTEST_PACK_S = 1.2


@dataclass(frozen=True)
class Region:
    """A stretch of speech, in seconds from the start of the recording."""

    start: float
    end: float


def joined(regions: Iterable[Region], duration: float, *, within: float = JOIN_WITHIN_S,
           pad: float = PAD_S, longest: float | None = None) -> list[Region]:
    """Regions in order, padded, kept inside the recording, near ones merged.

    Two regions are not merged into one longer than `longest`, and no region
    reaches back into the one before it.
    """
    out: list[Region] = []
    for region in sorted(regions, key=lambda r: r.start):
        start, end = max(0.0, region.start - pad), min(duration, region.end + pad)
        if out:
            start = max(start, out[-1].end)
        if end <= start:
            continue
        fits = longest is None or end - out[-1].start <= longest if out else False
        if fits and start - out[-1].end <= within:
            out[-1] = Region(out[-1].start, max(out[-1].end, end))
        else:
            out.append(Region(start, end))
    return out


def grouped(regions: Iterable[Region], longest: float, *, pause: float = PAUSE_S) -> list[list[Region]]:
    """Regions in runs that each pack into `longest` seconds or less.

    A region longer than that on its own is a run by itself.
    """
    runs: list[list[Region]] = []
    length = 0.0
    for region in regions:
        extra = region.end - region.start + pause
        if runs and length + extra <= longest:
            runs[-1].append(region)
            length += extra
        else:
            runs.append([region])
            length = extra - pause
    return runs


class Packed:
    """Where each region of a recording sits in its packed file."""

    def __init__(self, placed: Sequence[tuple[float, Region]]) -> None:
        self._starts = [at for at, _ in placed]
        self._regions = [region for _, region in placed]

    def original(self, packed_s: float) -> float:
        """A packed time as a time in the recording.

        A time in the silence after a region is the end of that region. With
        no regions packed there is nothing to map to, and the time is 0.
        """
        return self._clip(self._index(packed_s), packed_s) if self._regions else 0.0

    def span(self, start_s: float, end_s: float) -> tuple[float, float]:
        """A packed span as a span in the recording.

        A span that runs across a silence is kept to the region holding most
        of it: a word is said in one place, and a recogniser's times drift
        across a pause in both directions.
        """
        if not self._regions:
            return 0.0, 0.0
        first, last = self._index(start_s), self._index(end_s)
        best = max(range(first, last + 1), key=lambda index: self._overlap(index, start_s, end_s))
        return self._clip(best, start_s), self._clip(best, end_s)

    def _index(self, packed_s: float) -> int:
        return max(0, bisect_right(self._starts, packed_s) - 1)

    def _clip(self, index: int, packed_s: float) -> float:
        region = self._regions[index]
        return min(region.end, max(region.start, region.start + packed_s - self._starts[index]))

    def _overlap(self, index: int, start_s: float, end_s: float) -> float:
        at, region = self._starts[index], self._regions[index]
        return min(end_s, at + region.end - region.start) - max(start_s, at)


def pack(source: str | Path, target: str | Path, regions: Sequence[Region], *,
         pause: float = PAUSE_S, shortest: float = SHORTEST_PACK_S) -> Packed:
    """Write the regions of the WAV `source` to `target`, a pause between each.

    A file that would be shorter than `shortest` is filled out with silence.
    """
    placed: list[tuple[float, Region]] = []
    with wave.open(str(source), "rb") as recording, wave.open(str(target), "wb") as out:
        rate = recording.getframerate()
        out.setparams(recording.getparams())
        silence = bytes(recording.getsampwidth() * recording.getnchannels() * round(pause * rate))
        written = 0
        for region in regions:
            first = min(recording.getnframes(), max(0, round(region.start * rate)))
            count = min(recording.getnframes(), round(region.end * rate)) - first
            if count <= 0:
                continue
            if placed:
                out.writeframesraw(silence)
                written += round(pause * rate)
            recording.setpos(first)
            out.writeframesraw(recording.readframes(count))
            placed.append((written / rate, Region(first / rate, (first + count) / rate)))
            written += count
        missing = round(shortest * rate) - written
        if missing > 0:
            out.writeframesraw(bytes(recording.getsampwidth() * recording.getnchannels() * missing))
    return Packed(placed)


def duration(path: str | Path) -> float:
    """The length of a WAV file in seconds."""
    with wave.open(str(path), "rb") as recording:
        return recording.getnframes() / recording.getframerate()
