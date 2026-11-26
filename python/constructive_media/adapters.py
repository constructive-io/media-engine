"""Adapters: how the engine reaches every model and every effect.

The engine never imports a model library or an effects library by name. It
asks a registry for whatever fills a *slot* ("effects", "transcription", ...)
and is handed an object that does the job. Three things follow:

- Swapping a model is a setting, not a code change.
- Every slot has a built-in default, so the engine runs with nothing extra
  installed.
- A tool whose license cannot live in this repository is still usable: its
  adapter ships in a separate package and is found at run time.

A plugin is any installed package that declares an entry point in the
`constructive_media.adapters` group:

    [project.entry-points."constructive_media.adapters"]
    my_plugin = "my_plugin:adapters"

The entry point resolves to an `Adapter`, an iterable of them, or a function
that takes no arguments and returns either.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from importlib import metadata
from typing import Any

from .settings import Settings

ENTRY_POINT_GROUP = "constructive_media.adapters"
BUILTIN = "builtin"

#: Licenses a built-in adapter, and everything it imports, may carry.
PERMISSIVE = frozenset({"MIT", "Apache-2.0"})

#: Every slot the engine knows, and what fills it.
SLOTS: dict[str, str] = {
    "transcription": "speech to text, with word timings",
    "detection": "objects and people in a frame",
    "speech": "text to speech",
    "voice_conversion": "one voice re-spoken in another",
    "image": "image generation",
    "video": "video generation",
    "lip_sync": "a face re-timed to new speech",
    "effects": "audio effects",
    "text": "text generation and judgement",
    "embedding": "text and image embeddings",
}


class AdapterError(LookupError):
    """A slot or adapter that was asked for does not exist."""


class LicenseError(ValueError):
    """A built-in adapter carries a license this repository does not allow."""


@dataclass(frozen=True)
class Adapter:
    """One way of filling a slot.

    `factory` builds the working object and is the only place the adapter's
    dependencies are imported, so registering an adapter costs nothing until
    it is used. `license` is the SPDX identifier covering the adapter's code
    and everything it imports. `origin` is `builtin` for adapters in this
    repository and the plugin's distribution name otherwise.
    """

    slot: str
    name: str
    factory: Callable[[], Any]
    license: str
    summary: str = ""
    origin: str = BUILTIN


def lazy(target: str) -> Callable[[], Any]:
    """A factory for `"package.module:attribute"` that imports on first call."""
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"expected 'module:attribute', got {target!r}")

    def build() -> Any:
        return getattr(importlib.import_module(module_name), attribute)()

    return build


class Registry:
    """The adapters available to one engine."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], Adapter] = {}
        self._defaults: dict[str, str] = {}
        self._instances: dict[tuple[str, str], Any] = {}

    def register(self, adapter: Adapter, *, default: bool = False) -> Adapter:
        """Add an adapter. The first default registered for a slot stays."""
        if adapter.slot not in SLOTS:
            raise AdapterError(
                f"unknown slot {adapter.slot!r}; slots are: {', '.join(SLOTS)}"
            )
        if adapter.origin == BUILTIN and adapter.license not in PERMISSIVE:
            raise LicenseError(
                f"built-in adapter {adapter.slot}/{adapter.name} is {adapter.license}; "
                f"built-in adapters are {' or '.join(sorted(PERMISSIVE))} only. "
                "Ship it as a plugin."
            )
        key = (adapter.slot, adapter.name)
        if key in self._adapters:
            raise AdapterError(
                f"{adapter.slot}/{adapter.name} is already registered "
                f"by {self._adapters[key].origin}"
            )
        self._adapters[key] = adapter
        if default:
            self._defaults.setdefault(adapter.slot, adapter.name)
        return adapter

    def discover(self, entry_points: Iterable[Any] | None = None) -> list[Adapter]:
        """Register the adapters that installed plugins provide.

        A plugin that fails to load is reported as a warning and skipped: one
        broken plugin must not take the engine down with it.
        """
        if entry_points is None:
            entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
        found: list[Adapter] = []
        for entry_point in entry_points:
            origin = _origin(entry_point)
            try:
                for adapter in _adapters_from(entry_point.load()):
                    found.append(self.register(replace(adapter, origin=origin)))
            except Exception as error:  # noqa: BLE001 - any plugin failure is survivable
                warnings.warn(
                    f"constructive-media plugin {origin!r} was skipped: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
        return found

    def available(self, slot: str | None = None) -> list[Adapter]:
        """Every registered adapter, or the ones for one slot."""
        if slot is not None:
            _known(slot)
        return [a for a in self._adapters.values() if slot in (None, a.slot)]

    def describe(self, slot: str, name: str | None = None, *, settings: Settings | None = None) -> Adapter:
        """The adapter `get` would use, without building it.

        The name comes from the argument, then from settings, then from the
        slot's default.
        """
        _known(slot)
        chosen = name or (settings.adapter(slot) if settings else None) or self._defaults.get(slot)
        names = [a.name for a in self.available(slot)]
        if chosen is None:
            raise AdapterError(
                f"nothing fills the {slot!r} slot yet ({SLOTS[slot]}). "
                + (f"Choose one of: {', '.join(names)}." if names else "Install a plugin that provides one.")
            )
        try:
            return self._adapters[(slot, chosen)]
        except KeyError:
            raise AdapterError(
                f"no {slot!r} adapter named {chosen!r}. "
                + (f"Available: {', '.join(names)}." if names else "None are installed.")
            ) from None

    def get(self, slot: str, name: str | None = None, *, settings: Settings | None = None) -> Any:
        """The working object for a slot, built once and reused."""
        adapter = self.describe(slot, name, settings=settings)
        key = (adapter.slot, adapter.name)
        if key not in self._instances:
            self._instances[key] = adapter.factory()
        return self._instances[key]


def builtins() -> Iterator[Adapter]:
    """The adapters this repository ships. Each is the default for its slot."""
    yield Adapter(
        slot="effects",
        name="ffmpeg",
        factory=lazy("constructive_media.effects.ffmpeg:FfmpegEffects"),
        license="MIT",
        summary="Reverb, delay, compression and gain through the ffmpeg program",
    )


_registry: Registry | None = None


def registry() -> Registry:
    """The process-wide registry: built-ins first, then installed plugins."""
    global _registry
    if _registry is None:
        fresh = Registry()
        for adapter in builtins():
            fresh.register(adapter, default=True)
        fresh.discover()
        _registry = fresh
    return _registry


def get(slot: str, name: str | None = None, *, settings: Settings | None = None) -> Any:
    """The working object for a slot, from the process-wide registry."""
    return registry().get(slot, name, settings=settings or Settings.from_env())


def _known(slot: str) -> None:
    if slot not in SLOTS:
        raise AdapterError(f"unknown slot {slot!r}; slots are: {', '.join(SLOTS)}")


def _origin(entry_point: Any) -> str:
    distribution = getattr(entry_point, "dist", None)
    name = getattr(distribution, "name", None) or getattr(entry_point, "name", None)
    return name if name and name != BUILTIN else "plugin"


def _adapters_from(loaded: Any) -> Iterator[Adapter]:
    if callable(loaded) and not isinstance(loaded, Adapter):
        loaded = loaded()
    if isinstance(loaded, Adapter):
        loaded = [loaded]
    for adapter in loaded:
        if not isinstance(adapter, Adapter):
            raise TypeError(f"expected an Adapter, got {type(adapter).__name__}")
        yield adapter
