"""Where `@include` finds a sound bank.

The parser never touches a disk itself. It asks a finder for the bank a script
names, so a command reads files and a test keeps its banks in memory.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ENDING = ".sounds"


@dataclass(frozen=True)
class Bank:
    # What tells this bank from any other: it is how a circle of includes is
    # noticed, and where the bank's own includes are looked for from.
    id: str
    text: str


@dataclass(frozen=True)
class Missing:
    # Where it was looked for, to say so.
    tried: tuple[str, ...]


class Finder(Protocol):
    def find(self, name: str, origin: str | None) -> Bank | Missing:
        """The bank a script names. `origin` is the id of whatever includes it, when known."""

    def names(self) -> list[str]:
        """Every bank there is, to suggest one when a name is not found."""


class Memory:
    """Banks held in memory by name: `@include podcast` reads `banks["podcast"]`."""

    def __init__(self, banks: Mapping[str, str]):
        self.banks = dict(banks)

    def find(self, name: str, origin: str | None = None) -> Bank | Missing:
        name = name.removesuffix(ENDING)
        return Bank(name, self.banks[name]) if name in self.banks else Missing((name,))

    def names(self) -> list[str]:
        return sorted(self.banks)


def read(path: str | os.PathLike[str]) -> str:
    """A script or a bank as written, line endings and all."""
    return Path(path).read_bytes().decode("utf-8", errors="replace")


class Files:
    """Banks on disk.

    `@include podcast` reads `podcast.sounds` in the first of `banks` that has
    one. Anything with a `/` or a `.` in it is a path instead: a whole one, one
    from the home folder (`~/…`), or one beside the file that includes it and
    then under each of `roots`.
    """

    def __init__(self, banks: Sequence[str | os.PathLike[str]] = (),
                 roots: Sequence[str | os.PathLike[str]] = ()):
        self.banks = [os.path.abspath(folder) for folder in banks]
        self.roots = [os.path.abspath(folder) for folder in roots]

    def find(self, name: str, origin: str | None = None) -> Bank | Missing:
        if "/" not in name and "." not in name:
            tried = [os.path.join(folder, name + ENDING) for folder in self.banks]
        else:
            path = os.path.join(os.path.expanduser("~"), name[2:]) if name.startswith("~/") else name
            if os.path.isabs(path):
                tried = [path]
            else:
                beside = [os.path.dirname(origin)] if origin else []
                tried = [os.path.normpath(os.path.join(folder, path)) for folder in (*beside, *self.roots)]
        for path in tried:
            if os.path.isfile(path):
                return Bank(os.path.realpath(path), read(path))
        return Missing(tuple(tried))

    def names(self) -> list[str]:
        found = set()
        for folder in self.banks:
            if os.path.isdir(folder):
                found.update(name.removesuffix(ENDING) for name in os.listdir(folder) if name.endswith(ENDING))
        return sorted(found)
