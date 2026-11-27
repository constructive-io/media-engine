"""A take's microphones mixed into one sound that follows whoever is speaking.

One microphone for a whole passage is right while one person talks. When the
speaker changes inside it, everyone else is heard through somebody else's
microphone: further off, with more of the room, and quieter than the person
wearing it.

What an editor does is ride the faders. The microphone of whoever is talking
comes up, and the others come down but not away, because they carry the room,
the laughter and the answers that make a conversation sound like one. One
microphone alone loses those. All of them summed as they are give as many
copies of the room as there are microphones, each a little late on the last.

So each microphone gets a gain that moves through the stretch, worked out
from which is loudest against its own loud level, the same measure
`media microphones` gives a line a speaker by:

- **The gain moves smoothly.** A fader that snaps on every syllable sounds
  worse than one microphone used badly. It rises fast and a little early,
  since the first word of an answer is the one thing that cannot be missed,
  and falls slowly, so a breath between sentences does not drop the speaker.
- **The others are turned down, not off.** Turned off, the sound goes dead
  between sentences and every change of speaker is heard as a change in the
  hiss under it. How far down is a matter of taste, so it is a number to set.
- **When nobody speaks, the last speaker's microphone stays up.** Otherwise
  every pause would dip.
- **Everyone is brought to one level.** Recorders are set differently, so the
  person whose recorder was turned down would be quiet in the mix. Each
  microphone is scaled toward the middle of them, by a bounded amount.

Needs numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..audio import pcm
from ..video.loudness import levels
from . import Take, TakeError, members
from .microphones import FLOOR_DB, SILENT, Microphone, loud_level, worn

#: Samples a second the mix is made at.
RATE = 48000
#: Seconds between two values of a gain: finer than a syllable, coarser than a click.
HOP_S = 0.02
#: How far the other microphones are turned down while one leads, in decibels.
DUCK_DB = -12.0
#: How close to the loudest a microphone must be to be up as well, in decibels.
#: Without it two that are a fraction apart trade places all the time.
LEAD_DB = 3.0
#: How fast a gain rises, and how slowly it falls, in seconds.
ATTACK_S = 0.06
RELEASE_S = 0.35
#: How early a gain starts to rise, in seconds: the mix is made after the fact, so it can.
EARLY_S = 0.04
#: The most a microphone is turned up or down to bring everyone to one level, in decibels.
EVEN_DB = 12.0
#: How many gains' worth of sound is worked on at once: a minute, so an hour takes no more room than its samples.
BLOCK = 3000


@dataclass(frozen=True)
class Mixed:
    """One stretch of a take, mixed."""

    file: Path
    seconds: float
    #: The microphones that were mixed, by name, and the share of the stretch each led for.
    led: dict[str, float]
    #: What each was read from: the recording, and where in its own time the stretch begins and ends.
    heard: tuple[tuple[Any, float, float], ...] = ()


def follow(heard: list[Any], loud: list[float], *, rate: int = RATE, duck_db: float = DUCK_DB,
           lead_db: float = LEAD_DB, floor_db: float = FLOOR_DB) -> tuple[Any, list[float]]:
    """Mix sounds of one length, each with its own loud level. Returns the mix and the share each led for.

    Each sound is one channel of samples. A microphone leads a moment when it
    is the loudest against its own loud level and not far under it; when none
    is, the one that led last still does.
    """
    import numpy as np

    if not heard or len(heard) != len(loud):
        raise ValueError("a mix needs sounds, and a loud level for each")
    length = min(len(sound) for sound in heard)
    hop = max(1, round(rate * HOP_S))
    hops = length // hop
    if not hops:
        return np.zeros(length, dtype=np.float32), [0.0] * len(heard)
    level = np.empty((len(heard), hops))
    for index, sound in enumerate(heard):
        for first in range(0, hops, BLOCK):
            last = min(first + BLOCK, hops)
            blocks = np.asarray(sound[first * hop:last * hop], dtype=np.float64).reshape(last - first, hop)
            level[index, first:last] = 20.0 * np.log10(
                np.maximum(np.sqrt((blocks ** 2).mean(axis=1)), 1e-9) / max(loud[index], 1e-9))

    top = level.argmax(axis=0)
    speaking = level.max(axis=0) >= floor_db
    # Through a pause the last one to lead goes on leading; before anyone has, the first one to.
    if speaking.any():
        at = np.where(speaking, np.arange(hops), -1)
        at = np.maximum.accumulate(at)
        at[at < 0] = int(np.argmax(speaking))
        leader = top[at]
    else:
        leader = np.full(hops, int(np.bincount(top).argmax()))

    down = 10.0 ** (duck_db / 20.0)
    middle = float(np.median(loud))
    early = max(0, round(EARLY_S / HOP_S))
    rise, fall = 1.0 - math.exp(-HOP_S / ATTACK_S), 1.0 - math.exp(-HOP_S / RELEASE_S)
    moments = (np.arange(hops) + 0.5) * hop
    mixed = np.zeros(length, dtype=np.float32)
    for index, sound in enumerate(heard):
        up = (leader == index) | (speaking & (level[index] >= level.max(axis=0) - lead_db) & (level[index] >= floor_db))
        wanted = np.where(up, 1.0, down)
        for step in range(1, early + 1):        # up a little before it is needed
            wanted[:-step] = np.maximum(wanted[:-step], wanted[step:])
        gain = np.empty(hops)
        now = float(wanted[0])
        for moment in range(hops):
            now += (wanted[moment] - now) * (rise if wanted[moment] > now else fall)
            gain[moment] = now
        even = min(max(middle / max(loud[index], 1e-9), 10.0 ** (-EVEN_DB / 20.0)), 10.0 ** (EVEN_DB / 20.0))
        for first in range(0, length, BLOCK * hop):         # past the last gain, the last gain holds
            last = min(first + BLOCK * hop, length)
            moving = np.interp(np.arange(first, last), moments, gain) * even
            mixed[first:last] += (np.asarray(sound[first:last], dtype=np.float64) * moving).astype(np.float32)
    return mixed, [float((leader == index).mean()) for index in range(len(heard))]


def stretch(file: str | Path, start_s: float, seconds: float, *, rate: int = RATE) -> Any:
    """One channel of a file from `start_s` for `seconds`, silent wherever the file was not recording."""
    import numpy as np

    wanted = round(seconds * rate)
    before = min(wanted, round(max(0.0, -start_s) * rate))
    if before >= wanted:
        return np.zeros(wanted, dtype=np.float32)
    done = ffmpeg.run(
        ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start_s):.6f}",
        "-t", f"{(wanted - before) / rate:.6f}", "-i", file, "-vn", "-map", "0:a:0",
        "-af", f"aresample={rate}:{pcm.RESAMPLER}", "-f", "f32le", "-ac", "1", "-", binary=True,
    )
    read = np.frombuffer(done.stdout, dtype="<f4")[:wanted - before]
    whole = np.zeros(wanted, dtype=np.float32)
    whole[before:before + len(read)] = read
    return whole


class Mixer:
    """A take's microphones, ready to be mixed over any stretch of it.

    A microphone's loud level belongs to the whole recording and not to the
    stretch: someone who says four words in half a minute has a stretch whose
    loud level is the room, and measured against that their microphone would
    win every moment. So each is measured once, over all of its file.
    """

    def __init__(self, take: Take, *, duck_db: float = DUCK_DB, lead_db: float = LEAD_DB) -> None:
        self.take = take
        self.duck_db, self.lead_db = duck_db, lead_db
        self.microphones: list[Microphone] = []
        self.loud: list[float] = []
        self.lengths = {member.asset_id: member.duration_s for member in members(take.id)}
        for microphone in worn(take):
            if not Path(microphone.source).exists():
                raise TakeError(f"{microphone.name} is not where it was: {microphone.source}")
            found = levels(microphone.source)
            loud = loud_level(found[0]) if found else 0.0
            if loud >= SILENT:                  # one that recorded nothing has nothing to add
                self.microphones.append(microphone)
                self.loud.append(loud)

    @property
    def able(self) -> bool:
        """Whether there is anything to follow: two microphones that recorded something."""
        return len(self.microphones) >= 2

    def mix(self, start_s: float, end_s: float, out: str | Path) -> Mixed | None:
        """Mix a stretch of take time into `out`.

        None where there is nothing to follow: fewer than two microphones
        were recording, or none of them was for the whole of the stretch, and
        a mix with a hole in it is worse than the one recording it replaces.
        """
        import numpy as np

        seconds = end_s - start_s
        heard, loud, names, read, whole = [], [], [], [], False
        for microphone, level in zip(self.microphones, self.loud):
            own, length = start_s - microphone.offset_s, self.lengths.get(microphone.asset_id)
            if own + seconds <= 0 or (length is not None and own >= length):
                continue
            sound = stretch(microphone.source, own, seconds)
            if np.abs(sound).max(initial=0.0) <= 1e-5:
                continue
            whole = whole or (own >= 0 and (length is None or own + seconds <= length + HOP_S))
            heard.append(sound)
            loud.append(level)
            names.append(microphone.name)
            read.append((microphone.asset_id, max(own, 0.0), own + seconds if length is None else min(own + seconds, length)))
        if len(heard) < 2 or not whole:
            return None
        mixed, led = follow(heard, loud, duck_db=self.duck_db, lead_db=self.lead_db)
        return Mixed(pcm.write(out, mixed, RATE, bits=32), seconds, dict(zip(names, led)), tuple(read))
