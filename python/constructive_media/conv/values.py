"""Reading `key: value` lists and checking them against what each key takes.

Three ways of writing a list share one check:

    declaration  `{ persona: ada, prompt: "rain, distant thunder" }`  commas; quotes allowed
    overrides    `[excited, seed:303]`                                 commas; a bare word is an emotion
    play         `[GLASS vol:0.9, pan:0.2 hold]`                       commas or spaces; a bare word is a flag
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from .schema import ALIASES, Key, Registry, Schema, shown

_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_RADIX = re.compile(r"0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)\Z")
_YES_NO = {"true": True, "yes": True, "on": True, "false": False, "no": False, "off": False}
_SPACE = "﻿"


def trim(value: str) -> str:
    return value.strip().strip(_SPACE).strip()


def number(value: str) -> int | float | None:
    """A number as the format writes one, or None. A whole one comes back whole."""
    value = trim(value)
    if _RADIX.match(value):
        return int(value, 0)
    if not _NUMBER.match(value):
        return None
    read = float(value)
    return _whole(read) if math.isfinite(read) else None


def _whole(value: float) -> int | float:
    return int(value) if float(value).is_integer() and abs(value) < 2 ** 53 else value


def seconds(value: str) -> int | float | None:
    """Seconds, or `m:ss`, as seconds."""
    value = trim(value)
    if ":" not in value:
        return number(value)
    total: int | float = 0
    for part in value.split(":"):
        read = number(part)
        if read is None:
            return None
        total = total * 60 + read
    return _whole(total)


@dataclass(frozen=True)
class Pair:
    key: str                 # as the grammar knows it
    typed: str               # as the author wrote it
    value: str | bool        # the text, or True for a bare word


def canonical(key: str) -> str:
    key = trim(key).lower().replace("-", "_")
    return ALIASES.get(key, key)


def pair(part: str) -> Pair:
    key, colon, value = part.partition(":")
    if not colon:
        return Pair(canonical(part), trim(part), True)
    return Pair(canonical(key), trim(key), trim(value))


def split_top(raw: str) -> list[str]:
    """Split on the commas outside quotes; the quote marks themselves are dropped."""
    parts, part, quote = [], "", None
    for letter in raw:
        if quote:
            if letter == quote:
                quote = None
            else:
                part += letter
        elif letter in "\"'":
            quote = letter
        elif letter == ",":
            parts.append(part)
            part = ""
        else:
            part += letter
    parts.append(part)
    return parts


def read_declaration(raw: str, problems: list[str]) -> list[Pair]:
    pairs = []
    for part in split_top(raw):
        if not trim(part):
            continue
        read = pair(part)
        if read.value is True:
            problems.append(f"`{read.typed}` needs a value, e.g. `{read.typed}: …`")
            continue
        pairs.append(read)
    return pairs


def read_overrides(raw: str, problems: list[str]) -> list[Pair]:
    pairs, bare = [], []
    for part in raw.split(","):
        if not trim(part):
            continue
        read = pair(part)
        if read.value is True:
            bare.append(read.typed.lower())
        else:
            pairs.append(read)
    if len(bare) > 1:
        problems.append(f"`[{raw}]` names {len(bare)} emotions ({', '.join(bare)}) — a line has one. "
                        f"Pick one, e.g. `[{bare[0]}]`")
    if bare:
        pairs.append(Pair("emotion", "emotion", bare[-1]))
    return pairs


def read_play(raw: str | None) -> list[Pair]:
    return [pair(part) for part in re.split(r"[,\s]+", raw or "") if trim(part)]


def _matching(a: str, b: str) -> int:
    if not a or not b:
        return 0
    best = at_a = at_b = 0
    before = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        row = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                row[j] = before[j - 1] + 1
                if row[j] > best:
                    best, at_a, at_b = row[j], i - row[j], j - row[j]
        before = row
    if not best:
        return 0
    return best + _matching(a[:at_a], b[:at_b]) + _matching(a[at_a + best:], b[at_b + best:])


def similarity(a: str, b: str) -> float:
    """How alike two words are, from 0 to 1: twice what they share over what they hold."""
    total = len(a) + len(b)
    return 2 * _matching(a, b) / total if total else 1.0


def closest(word: str, options: Iterable[str], cutoff: float = 0.6) -> str | None:
    best, score = None, cutoff
    for option in options:
        alike = similarity(word, option)
        if alike >= score and (best is None or alike > score):
            best, score = option, alike
    return best


def did_you_mean(word: str, options: Iterable[str], show: Callable[[str], str] = str) -> str:
    """`. Did you mean `x`?` when one of the options is close, or nothing."""
    found = closest(word, options)
    return f". Did you mean `{show(found)}`?" if found else ""


def _bound(value: float | None) -> str:
    return str(_whole(value)) if value is not None else ""


@dataclass(frozen=True)
class Asking:
    """What a list of pairs is being checked for."""
    registry: Registry
    tracks: tuple[str, ...]      # every track in scope: built in, declared and included
    what: str                    # "track `crowd`", "a [music …] play": named when a key is unknown
    context: str | None = None


def _coerce(read: Pair, key: Key, asking: Asking) -> tuple[Any, str | None]:
    typed = read.typed
    said = "true" if read.value is True else str(read.value)
    bare = read.value is True
    if key.kind == "text":
        return (said, None) if trim(said) else (None, f"`{shown(read.key)}` is empty")
    if key.kind == "yes_no":
        if said.lower() in _YES_NO:
            return _YES_NO[said.lower()], None
        return None, f"`{typed}:{said}` — use true or false (yes/no also work)"
    if bare:
        return None, f"`{typed}` needs a value, e.g. `{typed}:…`"
    if key.kind == "whole":
        if re.match(r"-?[0-9]+\Z", said):
            return int(said), None
        if read.key == "seed":
            return None, f"`{typed}:{said}` — a seed must be a whole number, e.g. `seed:303`"
        return None, f"`{typed}:{said}` — {typed} must be a whole number"
    if key.kind == "time":
        read_s = seconds(said)
        if read_s is not None:
            return read_s, None
        return None, f"`{typed}:{said}` — {typed} must be seconds or m:ss, e.g. `{typed}:0:15`"
    if key.kind == "number":
        value = number(said)
        if value is None:
            return None, f"`{typed}:{said}` — {typed} must be a number"
        if value < key.low or value > key.high:
            return None, f"`{typed}:{said}` — out of range; use {_bound(key.low)} to {_bound(key.high)}"
        return value, None
    if key.kind == "choice":
        options = asking.registry.choices(key)
        if said.lower() in options:
            return said.lower(), None
        return None, f"`{typed}:{said}` — not one of: {', '.join(options)}" + did_you_mean(said.lower(), options)
    several = key.kind == "tracks"
    names = [trim(name) for name in re.split(r"[,+]", said) if trim(name)] if several else [said]
    for name in names:
        if several and name == "speech":
            continue
        if name not in asking.tracks:
            return None, (f"`{typed}:{said}` — no track named `{name}`. Tracks: {', '.join(sorted(asking.tracks))}"
                          + did_you_mean(name, asking.tracks)
                          + f"\nDeclare new tracks under @tracks, e.g. `{name}: {{ level: -24 }}`")
    return (names if several else said), None


def _at(read: Pair) -> tuple[Any, str | None]:
    said = "" if read.value is True else str(read.value)
    value = number(said)
    if value is None:
        return None, (f"`at:{said}` — at must be seconds: `at:18.5` from the start, "
                      "or `at:+2` / `at:-2` from this point")
    return {"mode": "offset" if said[:1] in ("+", "-") else "absolute", "seconds": value}, None


def check(pairs: Iterable[Pair], schema: Schema, asking: Asking, problems: list[str]) -> dict[str, Any]:
    """The pairs as checked values. What is wrong goes to `problems`, and that key is left out."""
    checked: dict[str, Any] = {}
    for read in pairs:
        # On a play or a fader move, `gain` multiplies as `vol` does.
        if read.key == "gain" and "gain" not in schema and "volume" in schema:
            read = replace(read, key="volume")
        key = schema.get(read.key)
        if key is None:
            if asking.context in ("line", "span") and read.key in ("persona", "voice", "speaker"):
                problems.append(f"`{read.typed}` can't be changed per line — set it on the character under "
                                "@characters, e.g. `DAN: { persona: dan }`")
                continue
            problems.append(f"unknown key `{shown(read.key)}` on {asking.what}"
                            + did_you_mean(read.key, schema, shown)
                            + f"\nValid keys: {', '.join(shown(known) for known in sorted(schema))}")
            continue
        value, wrong = _at(read) if read.key == "at" else _coerce(read, key, asking)
        if wrong:
            problems.append(wrong)
        else:
            checked[read.key] = value
    return checked
