"""The mix: named tracks, each summed and treated on its own, then added up.

A sound says what plays; a track says where. Every track runs one chain, in a
fixed order:

    clips -> effects -> fader -> ducking -> pan -> the mix

Each track after its chain is a stem. The stems add up to the mix, sample for
sample, so a mix written with its stems can be taken apart and put back
together in any editing program.

Sound is 32-bit floats from -1 to 1: `(n,)` for one channel, `(n, 2)` for two.
Nothing here clips. A sound that would pass the ceiling is turned down whole.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .. import effects as effect_slot
from ..effects import Effects
from ..settings import Settings
from . import pcm

#: The rate a mix runs at. Speech made at a lower rate is brought up to it.
RATE = 48_000

#: The loudest sample a mix or a levelled clip may hold.
CEILING = 0.97

#: The most a clip is turned up or down to reach its level, in dB. A clip
#: forty dB down is a failed recording, not a quiet one; it is left quiet, to
#: be noticed, and not turned up into its own noise.
MOST_GAIN = 24.0

SPEECH = "speech"


@dataclass(frozen=True)
class Track:
    """How one track behaves.

    `level` is the loudness its clips are brought to, in dB, or None to leave
    them as they come. `duck` is how far the track dips, in dB, while any of
    the `duck_under` tracks sounds; `speech` there stands for every track that
    carries a voice. A bed plays until something stops it; a one-shot plays
    once through.
    """

    name: str
    kind: str = "oneshot"
    level: float | None = -24.0
    volume: float = 1.0
    pan: float = 0.0
    duck: float | None = None
    duck_under: tuple[str, ...] = (SPEECH,)
    fade_in: float = 0.0
    fade_out: float = 0.0
    xfade: float = 0.0
    tail: float = 0.0
    loop: bool = False
    exclusive: bool = False
    effects: Effects = field(default_factory=Effects)

    @property
    def bed(self) -> bool:
        return self.kind == "bed"

    @classmethod
    def of(cls, name: str, declared: Mapping[str, Any] | None = None) -> Track:
        """A track by name: what it is by default, with a script's `@tracks` over it."""
        declared = declared or {}
        chosen = dict(BUILT_IN.get(name, CUSTOM))
        for known in fields(cls):
            if known.name in ("name", "effects") or declared.get(known.name) is None:
                continue
            value = declared[known.name]
            chosen[known.name] = tuple(value) if known.name == "duck_under" else value
        return cls(name=name, effects=Effects.from_overrides(declared), **chosen)


#: The tracks every script has. Speech and a video's own sound are kept as
#: they were made; everything else is brought to a level that sits under speech.
BUILT_IN: dict[str, dict[str, Any]] = {
    "dialogue": dict(level=None),
    "foreground": dict(level=-24.0, fade_out=0.3),
    "transition": dict(level=-22.0, fade_out=0.3),
    "video": dict(level=None, fade_out=0.05),
    "ambience": dict(kind="bed", level=-34.0, loop=True, fade_in=0.8, fade_out=1.2, xfade=0.5),
    "music": dict(kind="bed", level=-30.0, loop=True, exclusive=True, fade_out=3.0, xfade=2.0,
                  tail=2.0, duck=-8.0),
}

#: A track a script names for itself.
CUSTOM: dict[str, Any] = dict(level=-24.0, fade_out=1.0, xfade=1.0)


def tracks(declared: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Track]:
    """Every built-in track and every declared one, by name."""
    declared = declared or {}
    return {name: Track.of(name, declared.get(name)) for name in (*BUILT_IN, *declared)}


def loudness(samples: Any) -> float:
    """The level of a sound in dB: its root mean square against full scale."""
    import numpy as np

    samples = np.asarray(samples)
    if not samples.size:
        return -120.0
    return 20 * math.log10(max(float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))), 1e-9))


def peak(samples: Any) -> float:
    """The loudest sample of a sound in dB against full scale."""
    import numpy as np

    samples = np.asarray(samples)
    return 20 * math.log10(max(float(np.abs(samples).max()) if samples.size else 0.0, 1e-9))


def level(samples: Any, target: float, *, most: float = MOST_GAIN) -> Any:
    """Bring a clip to `target` dB without flattening it.

    The gain is the same for every sample, so a short loud moment in a quiet
    clip stays as sharp as it was. If that moment would pass the ceiling the
    whole clip is turned down to fit; it is never clipped.
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    if not samples.size:
        return samples
    gain = max(min(target - loudness(samples), most), -most)
    out = samples * np.float32(10 ** (gain / 20))
    top = float(np.abs(out).max())
    return out * np.float32(CEILING / top) if top > CEILING else out


def fit(samples: Any, seconds: float, rate: int = RATE, *, loop: bool = False, xfade: float = 0.0) -> Any:
    """Make a clip exactly `seconds` long: cut it, pad it with quiet, or loop it.

    A loop's every repeat is faded in over the end of the one before, for
    `xfade` seconds. A piece of music butted against its own beginning has a
    seam you can hear each time round.
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    wanted = int(round(seconds * rate))
    if wanted <= 0 or not len(samples):
        return np.zeros((max(wanted, 0), *samples.shape[1:]), dtype=np.float32)
    if len(samples) >= wanted:
        return samples[:wanted]
    if not loop:
        quiet = np.zeros((wanted - len(samples), *samples.shape[1:]), dtype=np.float32)
        return np.concatenate([samples, quiet])
    seam = int(xfade * rate)
    if seam <= 0 or seam * 2 >= len(samples):
        times = math.ceil(wanted / len(samples))
        return np.tile(samples, (times, *([1] * (samples.ndim - 1))))[:wanted]
    rise, fall = _over(_ramp(seam, True), samples), _over(_ramp(seam, False), samples)
    body, head = samples[seam:], samples[:seam]
    parts, length, last = [samples[:-seam]], len(samples) - seam, samples[-seam:]
    while length < wanted:
        parts += [last * fall + head * rise, body[:-seam]]
        length += len(body)
        last = body[-seam:]
    return np.concatenate([*parts, last])[:wanted]


def fade(samples: Any, rate: int = RATE, *, fade_in: float = 0.0, fade_out: float = 0.0) -> Any:
    """Fade a clip in and out along a quarter of a sine.

    A straight fade sounds as if it falls off a cliff halfway down. This one
    holds its level longer and lets go smoothly.
    """
    import numpy as np

    out = np.array(samples, dtype=np.float32)
    start, end = min(int(fade_in * rate), len(out)), min(int(fade_out * rate), len(out))
    if start > 0:
        out[:start] *= _over(_ramp(start, True), out)
    if end > 0:
        out[-end:] *= _over(_ramp(end, False), out)
    return out


def pan(samples: Any, where: float = 0.0) -> Any:
    """Place a sound between the left (-1) and the right (1), as two channels.

    One channel is spread at constant power, so a sound loses nothing as it
    moves off centre. Two channels are balanced: one side is turned down and
    the middle is left as it was.
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    where = max(-1.0, min(1.0, float(where or 0.0)))
    angle = (where + 1.0) * (math.pi / 4)
    if samples.ndim == 1:
        return np.stack([samples * np.float32(math.cos(angle)), samples * np.float32(math.sin(angle))], axis=1)
    if where == 0.0:
        return samples
    sides = [min(1.0, math.sqrt(2) * math.cos(angle)), min(1.0, math.sqrt(2) * math.sin(angle))]
    return samples * np.array(sides, dtype=np.float32)


#: The same, for where an argument is itself called `pan`.
_panned = pan


def duck(source: Any, rate: int = RATE, depth: float = -8.0, *, threshold: float | None = None,
         attack: float = 0.12, release: float = 0.6, hold: float = 0.3, hop: float = 0.01) -> Any:
    """The gain, sample by sample, that dips by `depth` dB wherever `source` sounds.

    `source` sounds where it is louder than `threshold`, or than 30 dB under
    its own loudest moment when none is given. The dip is held for `hold`
    seconds after the sound stops, so a bed does not pump between words, and
    it goes down and comes back smoothly, as a swell and not as a step.
    """
    import numpy as np

    source = np.asarray(source, dtype=np.float32)
    length = len(source)
    if not length or depth >= 0:
        return np.ones(length, dtype=np.float32)
    mono = np.abs(source).mean(axis=1) if source.ndim == 2 else np.abs(source)
    step = max(1, int(hop * rate))
    frames = math.ceil(length / step)
    padded = np.zeros(frames * step, dtype=np.float32)
    padded[:length] = mono
    loud = 20 * np.log10(np.sqrt(np.square(padded.reshape(frames, step)).mean(axis=1) + 1e-12) + 1e-9)
    floor = threshold if threshold is not None else max(-50.0, float(loud.max()) - 30.0)
    sounding = loud > floor
    held = int(hold / hop)
    if held > 0 and sounding.any():
        sounding = np.convolve(sounding, np.ones(held + 1))[:frames] > 0.5
    down, up = math.exp(-hop / max(attack, 1e-3)), math.exp(-hop / max(release, 1e-3))
    curve, now = [], 0.0
    for wanted in np.where(sounding, depth, 0.0).tolist():
        kept = down if wanted < now else up
        now = kept * now + (1 - kept) * wanted
        curve.append(now)
    gain = 10 ** (np.array(curve) / 20)
    return np.interp(np.arange(length), np.arange(frames) * step + step / 2, gain).astype(np.float32)


def ring(effects: Effects, backend: Any = None) -> float:
    """How many seconds a track's effects go on sounding after its last clip.

    A backend that knows how long its own effects ring says so; without one
    that does, this is an allowance wide enough for an ordinary room.
    """
    own = getattr(backend, "ring", None)
    if effects and callable(own):
        return max(0.0, float(own(effects)))
    seconds = 2.5 if effects.reverb else 0.0
    if effects.delay:
        feedback = 0.3 if effects.delay_feedback is None else effects.delay_feedback
        repeats = min(8, int(math.log(0.01) / math.log(max(feedback, 0.05))))
        seconds = max(seconds, effects.delay * (1 + repeats))
    return seconds


@dataclass(frozen=True)
class Mix:
    """A finished mix: the whole, each track on its own, and what was done."""

    samples: Any
    stems: dict[str, Any]
    rate: int
    report: dict[str, Any]


class Mixer:
    """Put clips on tracks, move faders, then mix.

        mixer = Mixer(tracks(scene["tracks"]))
        mixer.place("music", song, 0.0)
        mixer.place("dialogue", line, 1.5)
        mixer.write("scene.wav", stems="stems")

    `speech` names the tracks that carry voices. A track that ducks under
    `speech` ducks under all of them, so a voice sent to a track of its own
    still pulls the music down.

    `least` is how many seconds the mix lasts when its clips end sooner, so
    quiet at the end of a scene is kept.
    """

    def __init__(self, known: Mapping[str, Track] | None = None, *, rate: int = RATE,
                 backend: str | None = None, settings: Settings | None = None):
        self.rate = rate
        self.known = dict(known or {})
        self.backend = backend
        self.settings = settings
        self.speech: set[str] = {"dialogue"}
        self.least = 0.0
        self._clips: dict[str, list[tuple[float, Any]]] = {}
        self._moves: dict[str, list[tuple[float, float, float]]] = {}

    def track(self, name: str) -> Track:
        return self.known.get(name) or Track.of(name)

    def change(self, name: str, **settings: Any) -> Track:
        """Change how a track behaves for the whole mix."""
        self.known[name] = replace(self.track(name), **settings)
        return self.known[name]

    def place(self, track: str, samples: Any, start: float, *, pan: float = 0.0, volume: float = 1.0) -> None:
        """Put a clip on a track, `start` seconds in."""
        import numpy as np

        if samples is None or not len(samples):
            return
        clip = _panned(np.asarray(samples, dtype=np.float32) * np.float32(volume), pan)
        self._clips.setdefault(track, []).append((max(0.0, float(start)), clip))

    def move(self, track: str, at: float, volume: float, ramp: float = 0.0) -> None:
        """Move a track's fader to `volume` at `at`, over `ramp` seconds."""
        self._moves.setdefault(track, []).append((float(at), float(volume), float(ramp or 0.0)))

    @property
    def seconds(self) -> float:
        return self._length() / self.rate

    def mix(self) -> Mix:
        import numpy as np

        length = self._length()
        dry: dict[str, Any] = {}
        told: dict[str, dict[str, Any]] = {}
        for name, clips in self._clips.items():
            track = self.track(name)
            sound = np.zeros((length, 2), dtype=np.float32)
            for start, clip in clips:
                first = int(round(start * self.rate))
                last = min(length, first + len(clip))
                if last > first:
                    sound[first:last] += clip[:last - first]
            sound = self._treated(sound, track)
            if self._moves.get(name) or track.volume != 1.0:
                sound *= self._fader(name, length, track.volume)[:, None]
            dry[name] = sound
            told[name] = {"clips": len(clips), "effects": _asked(track.effects)}
        # A track ducks under what the others sound like before they are
        # ducked themselves, so two tracks can duck under each other.
        stems: dict[str, Any] = {}
        for name, sound in dry.items():
            track = self.track(name)
            under = [other for other in self._under(track) if other in dry and other != name]
            if track.duck is not None and track.duck < 0 and under:
                sound = sound * duck(sum(dry[other] for other in under), self.rate, track.duck)[:, None]
                told[name].update(ducked_under=under, duck_db=float(track.duck))
            stems[name] = pan(sound, track.pan)
        whole = np.zeros((length, 2), dtype=np.float32)
        for sound in stems.values():
            whole += sound
        top = float(np.abs(whole).max()) if length else 0.0
        report = {
            "seconds": round(length / self.rate, 3),
            "rate": self.rate,
            "clips": sum(len(clips) for clips in self._clips.values()),
            "peak_before_db": round(20 * math.log10(max(top, 1e-9)), 2),
            "limited": top > CEILING,
            "tracks": told,
        }
        if top > CEILING:
            # Turned down whole, and every stem with it, so they still add up.
            down = np.float32(CEILING / top)
            whole *= down
            stems = {name: sound * down for name, sound in stems.items()}
        report["peak_after_db"] = round(peak(whole), 2)
        return Mix(whole, stems, self.rate, report)

    def write(self, path: str | Path, *, stems: str | Path | None = None, bits: int = 24) -> dict[str, Any]:
        """Mix, write the mix to `path`, and each track to `stems` if a folder is given.

        Returns the report, with where everything was written.
        """
        done = self.mix()
        report = dict(done.report, path=str(pcm.write(path, done.samples, self.rate, bits=bits)))
        if stems is not None:
            report["stems"] = {name: str(file) for name, file in
                               write_stems(done, stems, bits=bits).items()}
        return report

    def _length(self) -> int:
        length = int(round(max(0.0, self.least) * self.rate))
        for name, clips in self._clips.items():
            wanted = self.track(name).effects
            after = ring(wanted, effect_slot.backend(self.backend, settings=self.settings)) if wanted else 0.0
            for start, clip in clips:
                length = max(length, int(round((start + after) * self.rate)) + len(clip))
        return length + 1

    def _under(self, track: Track) -> Iterable[str]:
        names: list[str] = []
        for name in track.duck_under:
            names += sorted(self.speech) if name == SPEECH else [name]
        return dict.fromkeys(names)

    def _fader(self, name: str, length: int, base: float) -> Any:
        import numpy as np

        curve = np.full(length, base, dtype=np.float32)
        now = base
        for at, volume, ramp in sorted(self._moves.get(name, [])):
            first = min(length, int(round(at * self.rate)))
            last = min(length, first + int(round(ramp * self.rate)))
            if last > first:
                curve[first:last] = np.linspace(now, volume, last - first, dtype=np.float32)
            curve[last:] = volume
            now = volume
        return curve

    def _treated(self, sound: Any, track: Track) -> Any:
        """A track's sound with its effects on, through the effects backend."""
        import numpy as np

        if not track.effects:
            return sound
        with tempfile.TemporaryDirectory(prefix="mix-") as folder:
            dry = pcm.write(Path(folder) / "dry.wav", sound, self.rate, bits=32)
            wet = effect_slot.apply(dry, Path(folder) / "wet.wav", track.effects,
                                    backend=self.backend, settings=self.settings)
            out = pcm.read(wet, rate=self.rate)
        if len(out) < len(sound):
            out = np.concatenate([out, np.zeros((len(sound) - len(out), 2), dtype=np.float32)])
        return out[:len(sound)]


def write_stems(mix: Mix, folder: str | Path, *, bits: int = 24) -> dict[str, Path]:
    """Write each track of a mix as `<folder>/<track>.wav`."""
    folder = Path(folder)
    written = {}
    for name, sound in mix.stems.items():
        if Path(name).name != name or name in (".", ".."):
            raise ValueError(f"a track named {name!r} cannot be written as a file")
        written[name] = pcm.write(folder / f"{name}.wav", sound, mix.rate, bits=bits)
    return written


def _asked(effects: Effects) -> dict[str, Any] | None:
    asked = {known.name: getattr(effects, known.name) for known in fields(effects)
             if getattr(effects, known.name) not in (None, False)}
    return asked or None


def _ramp(length: int, rising: bool) -> Any:
    import numpy as np

    curve = np.sin(np.linspace(0.0, 1.0, length, dtype=np.float32) * (math.pi / 2)).astype(np.float32)
    return curve if rising else curve[::-1]


def _over(curve: Any, samples: Any) -> Any:
    return curve[:, None] if samples.ndim == 2 else curve
