"""Audio effects: what a line asks for, apart from what runs it.

`Effects` is the request, in the vocabulary of the `.conv` format: a reverb
preset, a delay, compression, gain. An `EffectBackend` is whatever carries the
request out. The engine's default backend drives the `ffmpeg` program; others
arrive as plugins.

A bad value is an error. Nothing is silently ignored or defaulted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings

#: Reverb presets every backend offers. A backend may offer more.
REVERB_PRESETS = ("room", "hall", "cathedral", "plate")

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


class EffectError(ValueError):
    """An effect was asked for with a value it cannot take."""


@dataclass(frozen=True)
class Effects:
    """The effects asked of one piece of audio. Unset means "leave alone"."""

    reverb: str | None = None
    reverb_mix: float | None = None
    delay: float | None = None
    delay_feedback: float | None = None
    delay_mix: float | None = None
    compress: bool = False
    gain: float | None = None

    def __post_init__(self) -> None:
        _in_range("reverb-mix", self.reverb_mix, 0, 1)
        _in_range("delay", self.delay, 0.01, 5)
        _in_range("delay-feedback", self.delay_feedback, 0, 0.95)
        _in_range("delay-mix", self.delay_mix, 0, 1)
        _in_range("gain", self.gain, -60, 24)
        if self.reverb is None and self.reverb_mix is not None:
            raise EffectError("reverb-mix needs a reverb")
        if self.delay is None and (self.delay_feedback is not None or self.delay_mix is not None):
            raise EffectError("delay-feedback and delay-mix need a delay")

    def __bool__(self) -> bool:
        values = (getattr(self, f.name) for f in fields(self))
        return any(value is not None and value is not False for value in values)

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any]) -> Effects:
        """Pick the effect keys out of a `.conv` override map.

        Keys are spelled as the format spells them (`reverb-mix`) or as a
        parsed script spells them (`reverb_mix`). Keys that are not effects
        belong to someone else and are left alone.
        """
        picked: dict[str, Any] = {}
        for f in fields(cls):
            key = f.name.replace("_", "-")
            if key not in overrides:
                key = f.name
            if key not in overrides:
                continue
            raw = overrides[key]
            if f.name == "reverb":
                picked[f.name] = str(raw).strip().lower()
            elif f.name == "compress":
                picked[f.name] = _boolean(key, raw)
            else:
                picked[f.name] = _number(key, raw)
        return cls(**picked)


@runtime_checkable
class EffectBackend(Protocol):
    """Something that can carry out an `Effects` request."""

    name: str

    def apply(self, source: Path, target: Path, effects: Effects) -> Path:
        """Write `source` with `effects` applied to `target` and return it.

        `source` and `target` may be the same file. With no effects the audio
        is copied through unchanged. A WAV file of floats is answered with one:
        a track on its way into a mix may pass full scale, and whole numbers
        would clip it.
        """
        ...


def apply(
    source: str | Path,
    target: str | Path,
    effects: Effects,
    *,
    backend: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Apply effects with the chosen backend, or the configured default."""
    chosen: EffectBackend = adapters.get("effects", backend, settings=settings)
    return chosen.apply(Path(source), Path(target), effects)


def _in_range(key: str, value: float | None, low: float, high: float) -> None:
    if value is not None and not low <= value <= high:
        raise EffectError(f"{key} must be between {low:g} and {high:g}, got {value:g}")


def _number(key: str, raw: Any) -> float:
    if isinstance(raw, bool):
        raise EffectError(f"{key} must be a number, got {raw!r}")
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise EffectError(f"{key} must be a number, got {raw!r}") from None


def _boolean(key: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise EffectError(f"{key} must be yes or no, got {raw!r}")
