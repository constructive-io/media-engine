"""Checks made on the sound of a rendered line, and the mending of its edges.

A transcriber tidies what it hears. It writes a stuttered word once and
finishes a word that was cut off, so a line can read perfectly and sound
wrong. These listen to the sound itself.

Sound is one channel of floats from -1 to 1, with its rate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: The rate `repeats` is tuned at.
LISTENING_RATE = 16_000

TEXT = "text"


@dataclass(frozen=True)
class Repeat:
    """Two moments of a sound that are nearly the same sound."""

    first_s: float
    second_s: float
    gap_s: float
    similarity: float


def repeats(samples: Any, rate: int = LISTENING_RATE, *, window: float = 0.15, least_gap: float = 0.03,
            most_gap: float = 0.5, least_similarity: float = 0.90, floor: float = 0.01,
            hop: float = 0.01) -> list[Repeat]:
    """Moments that come again within half a second, the most alike first.

    A stutter is the same sound twice in quick succession. Each window of the
    sound is laid over the ones just after it, sample against sample, and a
    pair that agree almost exactly is a repeat. Nobody says a syllable the
    same way twice down to the sample; a model that stutters does. Windows
    too quiet to hold speech are not looked at.
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    width, step = int(window * rate), max(1, int(hop * rate))
    if len(samples) < width * 2:
        return []
    windows = np.lib.stride_tricks.sliding_window_view(samples, width)[:len(samples) - width:step]
    heard = np.sqrt(np.square(windows).mean(axis=1)) >= floor
    centred = windows - windows.mean(axis=1, keepdims=True)
    size = np.linalg.norm(centred, axis=1)
    usable = heard & (size >= 1e-6)
    found = []
    for apart in range(1, len(windows)):
        gap = apart * step / rate
        if gap < least_gap:
            continue
        if gap > most_gap:
            break
        both = usable[:-apart] & usable[apart:]
        if not both.any():
            continue
        firsts = np.flatnonzero(both)
        alike = np.einsum("ij,ij->i", centred[firsts], centred[firsts + apart]) / (size[firsts] * size[firsts + apart])
        for first, score in zip(firsts[alike >= least_similarity].tolist(), alike[alike >= least_similarity].tolist()):
            found.append(Repeat(first * step / rate, (first + apart) * step / rate, gap, float(score)))
    return sorted(found, key=lambda repeat: (-repeat.similarity, repeat.first_s, repeat.second_s))


def stutter(samples: Any, rate: int = LISTENING_RATE, **how: Any) -> Repeat | None:
    """The clearest repeat in a sound, or None when there is none."""
    found = repeats(samples, rate, **how)
    return found[0] if found else None


def cut_off(samples: Any, rate: int, *, window: float = 0.025, loudest: float = -20.0,
            least_seconds: float = 3.0) -> tuple[bool, float]:
    """Whether a line stops in the middle of a sound, and how loud its last moment is in dB.

    Speech dies away. A line whose last fortieth of a second is still within
    20 dB of its loudest moment did not end; it was stopped. Short lines are
    not judged: a single word can fairly end loud.
    """
    import numpy as np

    loud = np.abs(np.asarray(samples, dtype=np.float32))
    if not len(loud) or len(loud) / rate < least_seconds:
        return False, -120.0
    sounding = np.flatnonzero(loud > 1e-6)
    if not len(sounding):
        return False, -120.0
    end = int(sounding[-1])
    last = float(loud[max(0, end - int(rate * window)):end + 1].max())
    level = 20 * math.log10(max(last, 1e-9) / float(loud.max()))
    return level > loudest, round(level, 1)


def clean_start(samples: Any, rate: int, *, most_cut: float = 0.04, fade: float = 0.015,
                quiet: float = -40.0, dip: float = -18.0) -> Any:
    """A line with the quiet before it gone, a click at its start cut away, and a short fade in.

    Speech models often begin with a click: a burst a few thousandths of a
    second long, then a drop, then the voice. It is cut only when it is
    there, which is when the sound falls 18 dB under the burst within the
    first 40 thousandths of a second. A line that begins with a hard
    consonant has no such drop and keeps every sample.

    Do this once to a line. Done again, it looks for a click in what is
    already clean and may find the start of a word.
    """
    import numpy as np

    line = np.array(samples, dtype=np.float32)
    if not len(line) or float(np.abs(line).max()) <= 0:
        return line
    loud = np.abs(line)
    over = loud > (10 ** (quiet / 20.0)) * float(loud.max())
    line = line[int(np.argmax(over)):] if over.any() else line
    if len(line) < 2:
        return line
    width = max(1, int(rate * 0.005))
    frames = len(line) // width
    outline = (np.abs(line[:frames * width]).reshape(frames, width).max(axis=1) if frames
               else np.array([float(np.abs(line).max())]))
    look = outline[:max(1, int(rate * most_cut) // width) + 1]
    if len(look) >= 3 and float(look.max()) > 0:
        under = float(look.max()) * (10 ** (dip / 20.0))
        after = np.flatnonzero(look[int(np.argmax(look)) + 1:] < under)
        if len(after):
            line = line[min((int(np.argmax(look)) + 1 + int(after[0])) * width, len(line) - 1):]
    rise = int(rate * fade)
    if len(line) > rise > 0:
        line[:rise] *= np.linspace(0.0, 1.0, rise, dtype=np.float32)
    return line


def start_level(samples: Any, rate: int, *, head: float = 0.02) -> float:
    """How loud the first fiftieth of a second is against the loudest moment, in dB."""
    import numpy as np

    loud = np.abs(np.asarray(samples, dtype=np.float32))
    first = loud[:int(rate * head)]
    if not len(first) or float(loud.max()) <= 0:
        return -120.0
    return 20 * math.log10(max(float(first.max()), 1e-9) / float(loud.max()))


def join(pieces: Iterable[tuple[str, Any]], rate: int, *, crossfade: float = 0.03) -> Any:
    """Pieces of one line put end to end.

    Each piece is `(kind, samples)`. Where speech meets speech the two are
    faded across each other for a thirtieth of a second, which hides the
    small jump between two renderings of one voice. Where either side is
    anything else, a pause or a sound, they are simply put together: a fade
    into quiet would shorten the pause.
    """
    import numpy as np

    pieces = [(kind, np.asarray(samples, dtype=np.float32)) for kind, samples in pieces]
    if not pieces:
        raise ValueError("there is nothing to join")
    before, whole = pieces[0]
    for kind, samples in pieces[1:]:
        over = min(int(rate * crossfade), len(whole), len(samples)) if before == kind == TEXT else 0
        if over > 0:
            along = np.linspace(0.0, 1.0, over, dtype=np.float32)
            seam = whole[-over:] * np.sqrt(1.0 - along) + samples[:over] * np.sqrt(along)
            whole = np.concatenate([whole[:-over], seam, samples[over:]])
        else:
            whole = np.concatenate([whole, samples])
        before = kind
    return whole
