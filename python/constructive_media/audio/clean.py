"""Cleaning a recording of a voice: measure first, then mend only what is wrong.

A voice cloned from a recording copies everything in it, the hiss and the
hum as much as the person. So a recording is cleaned before a clip is cut
from it, and cleaned as little as will do, because every cleaner takes some
of the voice with the noise: breath, the rasp at the end of a phrase, the
edge of an `s`. A clone copies that loss as well.

    measure   how loud the voice is, how loud the quiet between its phrases
              is, and what that quiet is made of
    advise    what, if anything, is worth doing about it
    clean     do it, and measure again
    compare   what was gained, and whether the voice paid for it

What is measured is the quiet between phrases, since that is the only place
noise is heard alone. From it: how far the voice stands above the noise;
how much of the noise is rumble, under 80 Hz; how much sits where a voice's
own pitch does, from 80 to 400 Hz; how much is hiss, from 5 to 12 kHz; and
whether it hums at the mains.

    more than 45 dB between voice and noise   leave it alone
    rumble well above the rest                a high-pass filter, which no voice minds
    a hum at 50 or 60 Hz                      notches at it and what rings above it
    hiss, or noise where the voice is         a denoiser, and then a listen

A recording with no quiet in it at all has something under the voice all the
way through: music, a crowd, a room. That is not noise to take off. It is a
second sound, and taking a voice out from over one is separation, which
`media separate` is for.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..denoise import Denoiser
from ..denoise import denoiser as denoise_adapter
from ..settings import Settings
from . import pcm

#: The rate a recording is measured at: high enough to hear hiss up to 12 kHz.
RATE = 48_000
FRAME_S = 0.02
#: A stretch this far under the loudest is quiet, and what is in it is the noise.
QUIET_DB = 35.0
#: A recording that never drops this far under its loudest has no quiet in it.
LEAST_DROP_DB = 15.0
#: In a noisy recording, the quiet is what is within this of the quietest it gets.
NEAR_BOTTOM_DB = 6.0
#: Less quiet than this, in seconds, is too little to measure a noise by.
LEAST_QUIET_S = 0.3
#: With this much between voice and noise there is nothing worth mending.
CLEAN_DB = 45.0
#: Rumble this far over the noise where a voice sits is worth filtering off.
RUMBLE_DB = 6.0
#: Hiss within this of the noise where a voice sits is heard as hiss.
HISS_DB = 6.0
#: A harmonic of the mains this far over what is beside it is a hum.
HUM_DB = 12.0
MAINS = (50, 60)
HIGHPASS_HZ = 70
#: A voice that lost more than this to a cleaning was damaged by it.
DAMAGE_DB = 1.0
#: The least a cleaning should gain to have been worth doing.
GAIN_DB = 3.0

RUMBLE = (20.0, 80.0)
VOICE = (80.0, 400.0)
HISS = (5_000.0, 12_000.0)

STEPS = ("highpass", "notch", "denoise")


class CleanError(RuntimeError):
    """A recording could not be measured or cleaned."""


@dataclass(frozen=True)
class Noise:
    """What a recording is, as far as cleaning it goes. Levels are dB under full scale."""

    seconds: float
    quiet_s: float
    voice: float | None          #: how loud the voice is
    floor: float | None          #: how loud the quiet between phrases is; None when there is none
    snr: float | None            #: how far the voice stands over it
    rumble: float | None
    low: float | None            #: the noise where a voice's pitch sits
    hiss: float | None
    hum: int | None              #: 50 or 60 when it hums at the mains

    def told(self) -> dict[str, Any]:
        return {key: (round(value, 1) if isinstance(value, float) else value) for key, value in asdict(self).items()}


def measure(recording: str | Path) -> Noise:
    """Listen to the quiet between the phrases of a recording and say what is in it."""
    return _measured(recording)[0]


def _measured(recording: str | Path, where: tuple[Any, Any] | None = None) -> tuple[Noise, tuple[Any, Any]]:
    """What `measure` says, and where the voice and the quiet were found.

    Given `where` from another measuring, the same stretches are listened to:
    a recording and its cleaning are only compared fairly over the same ones.
    """
    recording = Path(recording)
    if not recording.is_file():
        raise CleanError(f"{recording}: no such file")
    import numpy as np

    try:
        heard = pcm.read(recording, rate=RATE, stereo=False)
    except ffmpeg.FfmpegError as error:
        raise CleanError(f"{recording} cannot be read as sound: {error}") from None
    size = int(RATE * FRAME_S)
    count = len(heard) // size
    if count < 10:
        raise CleanError(f"{recording} is too short to measure")
    frames = heard[: count * size].astype(np.float64).reshape(count, size)
    power = (frames ** 2).mean(axis=1)
    level = 10 * np.log10(power + 1e-12)
    # The quiet is what is under the voice by `QUIET_DB`, or, in a recording noisier than that, what is within
    # a little of the quietest it gets. One that never drops far has something sounding all the way through.
    if where is None:
        top, bottom = float(level.max()), float(np.percentile(level, 5))
        steady = top - bottom < LEAST_DROP_DB
        loud = level >= max(top - QUIET_DB, bottom + NEAR_BOTTOM_DB)
        # What is next to a phrase is the end of it, or the room answering it: not the noise alone.
        near = loud.copy()
        for shift in (1, 2, 3):
            near[shift:] |= loud[:-shift]
            near[:-shift] |= loud[shift:]
        quiet = ~near
    else:
        steady = False
        loud, quiet = (np.pad(mask[:count], (0, max(0, count - len(mask)))) for mask in where)
    seconds = len(heard) / RATE
    voice = float(10 * np.log10(power[loud].mean() + 1e-12)) if level.max() > -90 and loud.any() else None
    if voice is None or steady or quiet.sum() * FRAME_S < LEAST_QUIET_S:
        return Noise(seconds, float(quiet.sum() * FRAME_S), voice, None, None, None, None, None, None), (loud, quiet)
    floor = float(10 * np.log10(power[quiet].mean() + 1e-12))

    # The spectrum of the quiet: long windows for the hum, which is narrow, taken where the quiet runs long enough.
    window = 8192
    spectra = []
    start = None
    for index, is_quiet in enumerate([*quiet, False]):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            stretch = heard[start * size: index * size].astype(np.float64)
            start = None
            for begin in range(0, len(stretch) - window + 1, window // 2):
                spectra.append(np.abs(np.fft.rfft(stretch[begin: begin + window] * np.hanning(window))) ** 2)
    if not spectra:                                         # the quiet is all in short stretches
        short = size
        cut = frames[quiet] * np.hanning(short)
        spectrum = (np.abs(np.fft.rfft(cut, axis=1)) ** 2).mean(axis=0)
        width, long = RATE / short, False
    else:
        spectrum = np.mean(spectra, axis=0)
        width, long = RATE / window, True
    scale = 10 ** (floor / 10) / (spectrum.sum() + 1e-30)   # so the bands add up to the floor that was measured
    hum = _hum(spectrum * scale, width, voice) if long else None

    def band(low: float, high: float) -> float:
        part = spectrum[int(low / width): int(high / width) + 1]
        return float(10 * np.log10(part.sum() * scale + 1e-12))

    return (Noise(seconds, float(quiet.sum() * FRAME_S), voice, floor, voice - floor,
                  band(*RUMBLE), band(*VOICE), band(*HISS), hum), (loud, quiet))


def _hum(spectrum: Any, width: float, voice: float) -> int | None:
    """50 or 60 when the mains or what rings above it stand out of the noise beside them.

    A hum as far under the voice as a clean recording's noise is not one, however far it stands out of a silence.
    """
    import numpy as np

    found = {}
    for mains in MAINS:
        over, power = [], 0.0
        for harmonic in (1, 2, 3, 4):
            at = int(round(mains * harmonic / width))
            beside = np.concatenate([spectrum[max(1, at - 12): at - 2], spectrum[at + 3: at + 13]])
            over.append(10 * np.log10((spectrum[at - 1: at + 2].max() + 1e-30) / (np.median(beside) + 1e-30)))
            power += float(spectrum[at - 1: at + 2].sum())
        if sum(one >= HUM_DB for one in over) >= 2 and voice - 10 * np.log10(power + 1e-30) < CLEAN_DB:
            found[mains] = float(np.mean(sorted(over)[-2:]))
    return max(found, key=found.get) if found else None


def advise(noise: Noise) -> tuple[list[str], list[str]]:
    """What is worth doing to a recording, as steps for `clean`, and why, in words."""
    if noise.voice is None:
        return [], ["there is nothing to hear in it"]
    if noise.floor is None:
        return [], ["there is no quiet in it to measure a noise by. If something plays under the voice all the "
                    "way through, music or a crowd, that is not noise to take off: it is a second sound, and "
                    "taking a voice out from over one is separation, which no denoiser does and `media separate` "
                    "is for"]
    if noise.snr >= CLEAN_DB:
        return [], [f"the voice stands {noise.snr:.0f} dB over the quiet between its phrases: leave it alone"]
    steps, why = [], []
    if noise.rumble - noise.low >= RUMBLE_DB:
        steps.append("highpass")
        why.append(f"most of its noise is rumble under {RUMBLE[1]:.0f} Hz, {noise.rumble - noise.low:.0f} dB over "
                   "the rest: a high-pass filter takes it off, and no voice minds one")
    if noise.hum:
        steps.append("notch")
        why.append(f"it hums at {noise.hum} Hz: notches there and at what rings above it")
    # What a high-pass leaves behind may be nothing worth a denoiser.
    left = 10 * math.log10(max(1e-12, 10 ** (noise.floor / 10) - 10 ** (noise.rumble / 10)))
    if "highpass" in steps and noise.voice - left >= CLEAN_DB:
        return steps, why
    hissing = noise.hiss >= noise.low - HISS_DB
    if hissing or "highpass" not in steps:
        steps.append("denoise")
        why.append((f"it hisses: its noise from {HISS[0] / 1000:.0f} to {HISS[1] / 1000:.0f} kHz is as loud as "
                    "its noise lower down" if hissing else
                    "its noise sits where the voice does, which no filter can take off")
                   + ". A denoiser can, and takes a little of the voice with it: listen to what it gives back")
    return steps, why


def filters(steps: Sequence[str], *, hum: int | None = None, highpass: float = HIGHPASS_HZ) -> list[str]:
    """The ffmpeg filters for the steps that are filters, in the order they run."""
    chain = []
    if "highpass" in steps:
        chain += [f"highpass=f={highpass:g}:poles=2"] * 2
    if "notch" in steps:
        if hum not in MAINS:
            raise CleanError("a notch is cut at the mains: say which, 50 or 60")
        chain += [f"bandreject=f={hum * harmonic}:width_type=q:w=30" for harmonic in (1, 2, 3, 4, 5)]
    return chain


def clean(
    recording: str | Path, target: str | Path, *, steps: Sequence[str] | None = None, hum: int | None = None,
    highpass: float = HIGHPASS_HZ, denoiser: Denoiser | None = None, settings: Settings | None = None,
) -> dict[str, Any]:
    """Clean `recording` and write it as `target`: one channel, at the rate it came at.

    `steps` are any of `highpass`, `notch` and `denoise`; left out, they are
    what `advise` says the recording needs, which may be nothing, and then
    nothing is written. Returns what was done and what it did: the recording
    as measured before and after, how much quieter its noise is, and how much
    the voice lost. `damaged` is true when the voice lost more than a
    decibel, which is when a cleaning has cost more than it gave.
    """
    recording, target = Path(recording), Path(target)
    before, where = _measured(recording)
    why: list[str] = []
    if steps is None:
        steps, why = advise(before)
    unknown = [step for step in steps if step not in STEPS]
    if unknown:
        raise CleanError(f"{unknown[0]} is not a way to clean a recording: {', '.join(STEPS)}")
    hum = hum or before.hum
    if not steps:
        return {"input": str(recording), "output": None, "steps": [], "why": why, "before": before.told()}
    chain = filters(steps, hum=hum, highpass=highpass)
    rate = min(RATE, pcm.rate(recording))
    with tempfile.TemporaryDirectory(prefix="clean-") as scratch:
        at = Path(scratch) / "filtered.wav"
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", recording, "-vn",
                   *(("-af", ",".join(chain)) if chain else ()), "-ac", "1", "-c:a", "pcm_f32le", at)
        used = None
        if "denoise" in steps:
            cleaner = denoiser if denoiser is not None else denoise_adapter(settings=settings)
            floor = _measured(at, where)[0].floor if chain else before.floor
            at = Path(cleaner.clean(at, Path(scratch) / "denoised.wav", floor=floor))
            used = cleaner.name
        target.parent.mkdir(parents=True, exist_ok=True)
        kind = ("-c:a", "pcm_s24le") if target.suffix.lower() == ".wav" else ()
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", at, "-vn",
                   "-af", f"aresample={rate}:{pcm.RESAMPLER}", "-ac", "1", *kind, target)
    after = _measured(target, where)[0]                     # over the same stretches as before
    return {"input": str(recording), "output": str(target), "steps": list(steps), "why": why, "denoiser": used,
            "hum": hum if "notch" in steps else None, "before": before.told(), "after": after.told(),
            **compare(before, after)}


def compare(before: Noise, after: Noise) -> dict[str, Any]:
    """What a cleaning gained, in dB, and what the voice lost to it."""
    if None in (before.snr, after.snr):
        return {"gained": None, "voice_lost": None, "damaged": False, "worth_it": None}
    lost = before.voice - after.voice
    gained = after.snr - before.snr
    return {"gained": round(gained, 1), "voice_lost": round(lost, 2), "damaged": lost > DAMAGE_DB,
            "worth_it": gained >= GAIN_DB and lost <= DAMAGE_DB}
