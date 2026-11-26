"""Keys and the values they take, in each place a key can be written.

The core grammar has its own keys. Everything else, effects included, is a
plugin: it adds keys to a context, values to a named list, tracks every scene
has, directives for the body, and checks on the finished scene. A registry is
the grammar with a set of plugins folded in, and the parser checks every key
against it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

CONTEXTS = ("character", "line", "span", "sound", "track", "play", "off", "set")


@dataclass(frozen=True)
class Key:
    """What a key's value has to be."""
    kind: str
    low: float | None = None
    high: float | None = None
    # For a choice: the values themselves, or the name of a list plugins add to.
    values: tuple[str, ...] | str | None = None


def text() -> Key:
    return Key("text")


def whole() -> Key:
    return Key("whole")


def number(low: float, high: float) -> Key:
    return Key("number", low, high)


def yes_no() -> Key:
    return Key("yes_no")


def time() -> Key:
    """Seconds, or `m:ss`."""
    return Key("time")


def one_of(*values: str) -> Key:
    return Key("choice", values=tuple(values))


def named(name: str) -> Key:
    """One of a named list, which plugins can add to."""
    return Key("choice", values=name)


def track() -> Key:
    """The name of a track the scene has."""
    return Key("track")


def tracks() -> Key:
    """A list of tracks, `a,b` or `a+b`; `speech` stands for every track speech goes to."""
    return Key("tracks")


Schema = dict[str, Key]

CORE: dict[str, Schema] = {
    "character": {
        "persona": text(), "voice": text(), "speaker": text(), "emotion": text(),
        "track": track(), "seed": whole(), "gain": number(-60, 24),
    },
    "line": {"emotion": text(), "seed": whole(), "track": track(), "gain": number(-60, 24)},
    "span": {"emotion": text(), "seed": whole(), "gain": number(-60, 24)},
    "sound": {
        "prompt": text(), "file": text(), "duration": number(0.05, 3600), "seed": whole(),
        "loop": yes_no(), "level": number(-80, 0), "start": time(), "layer": track(), "track": track(),
    },
    "track": {
        "kind": one_of("bed", "oneshot"), "level": number(-80, 0), "volume": number(0, 4),
        "gain": number(-60, 24), "pan": number(-1, 1), "duck": number(-60, 0), "duck_under": tracks(),
        "fade_in": number(0, 60), "fade_out": number(0, 60), "xfade": number(0, 30),
        "tail": number(0, 60), "loop": yes_no(), "exclusive": yes_no(),
    },
    "play": {
        "at": number(-3600, 36000), "volume": number(0, 4), "pan": number(-1, 1),
        "fade_in": number(0, 60), "fade_out": number(0, 60), "duration": number(0.05, 3600),
        "seed": whole(), "start": time(), "for": time(), "hold": yes_no(), "loop": yes_no(),
        "noloop": yes_no(), "level": number(-80, 0), "xfade": number(0, 30),
    },
    "off": {"fade_out": number(0, 60)},
    "set": {"volume": number(0, 4), "ramp": number(0, 600), "at": number(-3600, 36000)},
}

# Tracks every scene has without declaring them.
TRACKS = ("dialogue", "foreground", "ambience", "music", "transition")

# Lowercase words that can never name a track.
RESERVED = ("pause", "off", "track")

# Other spellings an author may use for a key.
ALIASES = {
    "vol": "volume", "fadein": "fade_in", "fadeout": "fade_out",
    "dur": "duration", "from": "start", "length": "for",
}


def shown(key: str) -> str:
    """A key as it is shown back to an author."""
    return "vol" if key == "volume" else key.replace("_", "-")


# `[name ...]` in the body: given what follows the name and somewhere to report a
# problem, it returns what the mark holds.
Directive = Callable[[str, Callable[[str], None]], "dict[str, Any] | None"]
# A check on a directive that names one track; it may also settle the item.
TrackRule = Callable[[dict[str, Any], Callable[[str], None]], None]
# A pass over the finished scene, reporting problems against an item.
Finalize = Callable[[dict[str, Any], Callable[[dict[str, Any], str], None]], None]


@dataclass(frozen=True)
class Plugin:
    """Something added to the grammar."""
    name: str
    keys: Mapping[str, Mapping[str, Key]] = field(default_factory=dict)
    enums: Mapping[str, Sequence[str]] = field(default_factory=dict)
    tracks: Sequence[str] = ()
    reserved: Sequence[str] = ()
    directives: Mapping[str, Directive] = field(default_factory=dict)
    track_rules: Mapping[str, TrackRule] = field(default_factory=dict)
    finalize: Finalize | None = None


@dataclass
class Registry:
    """The grammar with its plugins folded in."""
    schemas: dict[str, Schema]
    enums: dict[str, list[str]]
    tracks: list[str]
    reserved: set[str]
    directives: dict[str, Directive]
    track_rules: dict[str, list[TrackRule]]
    finalizers: list[Finalize]

    def choices(self, key: Key) -> list[str]:
        return self.enums.get(key.values, []) if isinstance(key.values, str) else list(key.values or ())


def registry(plugins: Sequence[Plugin]) -> Registry:
    made = Registry(
        schemas={context: dict(CORE[context]) for context in CONTEXTS}, enums={}, tracks=list(TRACKS),
        reserved=set(RESERVED), directives={}, track_rules={}, finalizers=[],
    )
    owner: dict[str, str] = {}
    for plugin in plugins:
        for context, keys in plugin.keys.items():
            made.schemas[context].update(keys)
        for name, values in plugin.enums.items():
            have = made.enums.setdefault(name, [])
            have.extend(value for value in values if value not in have)
        made.tracks.extend(name for name in plugin.tracks if name not in made.tracks)
        made.reserved.update(plugin.reserved)
        for name, directive in plugin.directives.items():
            if name in made.directives:
                raise ValueError(f'conv plugins "{owner[name]}" and "{plugin.name}" both define the [{name}] directive')
            if name in made.tracks:
                raise ValueError(f'conv plugin "{plugin.name}": [{name}] is already a track')
            made.directives[name] = directive
            made.reserved.add(name)
            owner[name] = plugin.name
        for name, rule in plugin.track_rules.items():
            made.track_rules.setdefault(name, []).append(rule)
        if plugin.finalize:
            made.finalizers.append(plugin.finalize)
    return made
