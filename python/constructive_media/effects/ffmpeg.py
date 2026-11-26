"""The default effects backend: the `ffmpeg` program.

ffmpeg is run as a separate program and is not part of this package. It has no
algorithmic reverb, so a space is built from a few echo taps: more, longer and
quieter taps for a bigger room. It reads as a space; it is not a convolution
reverb. A delay is a train of taps, each quieter than the last by the feedback
amount.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..ffmpeg import FfmpegError, probe
from . import EffectError, Effects

#: ffmpeg's echo filter takes delays up to this many milliseconds.
MAX_ECHO_MS = 90_000

#: The quietest repeat worth rendering, and the most repeats of one delay.
QUIETEST_TAP = 0.05
MAX_TAPS = 6

DEFAULT_DELAY_FEEDBACK = 0.3
DEFAULT_DELAY_MIX = 0.3


@dataclass(frozen=True)
class Space:
    """A reverb preset: echo taps and how wet the preset is by default."""

    delays_ms: tuple[int, ...]
    decays: tuple[float, ...]
    mix: float


SPACES: dict[str, Space] = {
    "room": Space((35,), (0.28,), 0.12),
    "hall": Space((55, 95), (0.35, 0.22), 0.20),
    "cathedral": Space((90, 150, 230), (0.42, 0.30, 0.18), 0.30),
    "plate": Space((18, 30), (0.30, 0.18), 0.18),
}


class FfmpegEffects:
    """Reverb, delay, compression and gain, as one ffmpeg filter chain."""

    name = "ffmpeg"

    def __init__(self, binary: str = "ffmpeg") -> None:
        self.binary = binary

    def filters(self, effects: Effects) -> list[str]:
        """The ffmpeg audio filters for a request, in the order they run."""
        chain: list[str] = []
        if effects.reverb is not None:
            chain.append(_reverb(effects.reverb, effects.reverb_mix))
        if effects.delay is not None:
            chain.append(_delay(effects.delay, effects.delay_feedback, effects.delay_mix))
        if effects.compress:
            chain.append("acompressor=threshold=-18dB:ratio=3")
        if effects.gain is not None:
            chain.append(f"volume={effects.gain:g}dB")
        return chain

    def apply(self, source: Path, target: Path, effects: Effects) -> Path:
        source, target = Path(source), Path(target)
        chain = self.filters(effects)
        if not chain:
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            return target
        if shutil.which(self.binary) is None:
            raise RuntimeError(
                f"the {self.binary!r} program was not found; install ffmpeg to use audio effects"
            )
        # Never read and write one file in the same ffmpeg run.
        scratch = target.with_name(f".{target.stem}.fx{target.suffix}")
        command = [self.binary, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(source), "-af", ",".join(chain)]
        if target.suffix.lower() == ".wav":
            command += ["-c:a", _wav_codec(source)]
        command.append(str(scratch))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            os.replace(scratch, target)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"ffmpeg failed: {error.stderr.strip()}") from error
        finally:
            scratch.unlink(missing_ok=True)
        return target


def _wav_codec(source: Path) -> str:
    """The WAV sample format that loses nothing the source had.

    Floats stay floats, so sound that passes full scale is not clipped; deep
    whole numbers stay deep; anything else is written as 16 bit.
    """
    try:
        found = probe(source)
    except FfmpegError:
        found = None
    codec = (found.codec if found else None) or ""
    if codec.startswith(("pcm_f32", "pcm_f64")):
        return "pcm_f32le"
    if codec.startswith(("pcm_s24", "pcm_s32")):
        return "pcm_s24le"
    return "pcm_s16le"


def _reverb(preset: str, mix: float | None) -> str:
    try:
        space = SPACES[preset]
    except KeyError:
        raise EffectError(
            f"unknown reverb {preset!r}; this backend offers: {', '.join(SPACES)}"
        ) from None
    wetness = 1.0 if mix is None else mix / space.mix
    decays = [min(1.0, decay * wetness) for decay in space.decays]
    return _echo(space.delays_ms, decays)


def _delay(seconds: float, feedback: float | None, mix: float | None) -> str:
    feedback = DEFAULT_DELAY_FEEDBACK if feedback is None else feedback
    level = DEFAULT_DELAY_MIX if mix is None else mix
    step_ms = max(1, round(seconds * 1000))
    delays: list[int] = []
    decays: list[float] = []
    while len(delays) < MAX_TAPS and step_ms * (len(delays) + 1) <= MAX_ECHO_MS:
        if delays and level < QUIETEST_TAP:
            break
        delays.append(step_ms * (len(delays) + 1))
        decays.append(level)
        level *= feedback
    return _echo(delays, decays)


def _echo(delays_ms: list[int] | tuple[int, ...], decays: list[float] | tuple[float, ...]) -> str:
    audible = [(d, k) for d, k in zip(delays_ms, decays) if k > 0]
    if not audible:
        return "anull"
    return "aecho=0.8:0.7:{}:{}".format(
        "|".join(str(d) for d, _ in audible),
        "|".join(f"{k:.3g}" for _, k in audible),
    )
