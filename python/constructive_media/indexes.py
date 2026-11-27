"""Indexes: where a library is found by its name.

A library is added by its address. An index is a list of libraries with
their addresses, so that one can be added by a name and looked for by what it
holds. It is a folder, most often a git repository, with one file at its top:

    media-index.json
    {
      "index": "harbour",
      "summary": "Libraries kept by the harbour office.",
      "libraries": [
        {"name": "lighthouse", "source": "harbour-office/lighthouse-voices",
         "summary": "The keepers of the light, and the weather they keep it in.",
         "license": "CC-BY-4.0", "holds": ["voices", "banks", "faces"], "tags": ["weather"]}
      ]
    }

`source` is what `me library add` would be given: `owner/name` on GitHub, or
an address that starts `https://`, `ssh://`, `git@` or `file://`. Nothing else
is taken from an index, since an index is somebody else's file.

Which indexes this machine reads is a setting, `CONSTRUCTIVE_MEDIA_LIBRARY_INDEX`:
folders or addresses with commas between, the first to name a library winning.
An index at an address is cloned under the engine's home and looked at again
once a day, or when asked.

An index says where a library is. It does not vouch for it: the library is
read and checked when it is added, as any other is, and one that is not
called what the index calls it is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import Settings

INDEX = "media-index.json"
#: What a library can be said to hold.
HOLDS = ("voices", "banks", "faces")
#: How long a clone of an index is used before it is looked at again, in seconds.
FRESH = 24 * 60 * 60

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHORT = re.compile(r"[A-Za-z0-9][\w.-]*/(?!\.{1,2}$)[\w.-]+")       # owner/name, and never a path such as ../name
_FETCHED = ("https://", "ssh://", "git@", "file://")
_KNOWN = {"index", "summary", "homepage", "libraries"}
_OF_A_LIBRARY = {"name", "source", "summary", "license", "holds", "tags", "homepage"}


@dataclass(frozen=True)
class Listed:
    """A library as an index lists it."""

    name: str
    source: str
    summary: str | None
    license: str | None
    holds: tuple[str, ...]
    tags: tuple[str, ...]
    index: str


@dataclass(frozen=True)
class CheckedIndex:
    name: str | None
    listed: tuple[Listed, ...]
    problems: tuple[str, ...]
    notes: tuple[str, ...]


def is_index(folder: str | Path) -> bool:
    return (Path(folder).expanduser() / INDEX).is_file()


def check(folder: str | Path) -> CheckedIndex:
    """Read the index in `folder` and say everything wrong with it."""
    folder = Path(folder).expanduser()
    written = folder / INDEX
    if not written.is_file():
        return CheckedIndex(None, (), (f"{folder} is not an index: it has no {INDEX}",), ())
    try:
        index = json.loads(written.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return CheckedIndex(None, (), (f"{written} cannot be read as JSON: {error}",), ())
    if not isinstance(index, dict):
        return CheckedIndex(None, (), (f"{written} should hold an object, in braces",), ())

    problems: list[str] = []
    notes: list[str] = []
    name = index.get("index")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        problems.append('"index" is the index\'s name: lower case letters and numbers, joined by hyphens')
        name = None
    for key in sorted(set(index) - _KNOWN):
        notes.append(f'"{key}" is not something an index says; it is ignored')

    given = index.get("libraries", [])
    if not isinstance(given, list):
        problems.append('"libraries" should be a list')
        given = []
    listed: list[Listed] = []
    seen: set[str] = set()
    for number, one in enumerate(given, start=1):
        if not isinstance(one, dict):
            problems.append(f"library {number} should be an object, in braces")
            continue
        called = one.get("name")
        which = f"library {number}"
        if not isinstance(called, str) or not _NAME.fullmatch(called):
            problems.append(f'{which}: "name" is what the library calls itself: lower case, with hyphens')
            called = None
        else:
            which = f"`{called}`"
            if called in seen:
                problems.append(f"{which} is listed twice: a name is one library")
            seen.add(called)
        source = one.get("source")
        if not isinstance(source, str) or not fetched(source):
            problems.append(f'{which}: "source" is where the library is: owner/name on GitHub, or an address that '
                            "starts https://, ssh:// or git@")
            source = None
        for key in ("summary", "license", "homepage"):
            if key in one and (not isinstance(one[key], str) or not one[key].strip()):
                problems.append(f'{which}: "{key}" should be a line of words')
        if not one.get("summary"):
            notes.append(f"{which} has no summary, so a search finds it only by its name")
        holds = _words(one.get("holds", []), f'{which}: "holds"', problems)
        for what in holds:
            if what not in HOLDS:
                problems.append(f'{which}: "holds" names `{what}`. A library holds any of {", ".join(HOLDS)}')
        tags = _words(one.get("tags", []), f'{which}: "tags"', problems)
        for key in sorted(set(one) - _OF_A_LIBRARY):
            notes.append(f'{which}: "{key}" is not something an index says of a library; it is ignored')
        if called and source:
            listed.append(Listed(called, source, _line(one.get("summary")), _line(one.get("license")),
                                 tuple(what for what in holds if what in HOLDS), tags, name or ""))
    if not given and not problems:
        notes.append("it lists no libraries yet")
    return CheckedIndex(name, tuple(listed), tuple(problems), tuple(notes))


def fetched(source: str) -> bool:
    """Whether `source` is an address an index may name: never a path, an option or a program to run."""
    source = source.strip()
    if not source or source.startswith("-") or "::" in source or any(mark in source for mark in "\n\r\0 "):
        return False
    return bool(_SHORT.fullmatch(source)) or source.startswith(_FETCHED)


def _line(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _words(value: Any, what: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(word, str) and word.strip() for word in value):
        problems.append(f"{what} should be a list of words")
        return ()
    return tuple(word.strip().lower() for word in value)


# The indexes of this machine.

def listed(settings: Settings | None = None, *, refresh: bool = False,
           tell: Callable[[str], None] | None = None) -> list[Listed]:
    """Every library the indexes of this machine list. The first index to name a library is the one believed.

    An index that cannot be reached or read is passed over, and `tell` hears why.
    """
    settings = settings or Settings.from_env()
    said = tell or (lambda message: None)
    found: dict[str, Listed] = {}
    for address in settings.indexes:
        try:
            folder = _here(address, settings, refresh)
        except IndexUnreached as error:
            said(str(error))
            continue
        checked = check(folder)
        if checked.problems:
            said(f"the index at {address} was passed over: {checked.problems[0]}")
            continue
        for one in checked.listed:
            found.setdefault(one.name, one)
    return sorted(found.values(), key=lambda one: one.name)


def find(name: str, settings: Settings | None = None, *, refresh: bool = False,
         tell: Callable[[str], None] | None = None) -> Listed | None:
    """The library an index lists under `name`, or None."""
    for one in listed(settings, refresh=refresh, tell=tell):
        if one.name == name:
            return one
    return None


def search(words: Iterable[str], settings: Settings | None = None, *, refresh: bool = False,
           tell: Callable[[str], None] | None = None) -> list[Listed]:
    """The listed libraries that every one of `words` is found in: by name, summary, tag or what they hold."""
    wanted = [word.lower() for word in words if word.strip()]

    def about(one: Listed) -> str:
        return " ".join((one.name, one.summary or "", one.license or "", *one.holds, *one.tags)).lower()

    return [one for one in listed(settings, refresh=refresh, tell=tell)
            if all(word in about(one) for word in wanted)]


class IndexUnreached(Exception):
    """An index that could not be fetched, with nothing kept of it from before."""


def _here(address: str, settings: Settings, refresh: bool) -> Path:
    """The folder an index is in on this machine, cloning or fetching it when it is at an address."""
    from .libraries import LibraryError, _git

    local = Path(address).expanduser()
    if local.is_dir():
        return local
    if not fetched(address):
        raise IndexUnreached(f"the index `{address}` is neither a folder nor an address: owner/name on GitHub, "
                             "or one that starts https://, ssh:// or git@")
    remote = f"https://github.com/{address}.git" if _SHORT.fullmatch(address) else address
    kept = settings.home / "indexes"
    folder = kept / (re.sub(r"[^a-z0-9]+", "-", address.lower()).strip("-")[:48] + "-"
                     + hashlib.sha256(address.encode()).hexdigest()[:8])
    stamp = folder / ".git" / "constructive-media-fetched"
    if folder.is_dir():
        try:
            fresh = time.time() - stamp.stat().st_mtime < FRESH
        except OSError:
            fresh = False
        if fresh and not refresh:
            return folder
        try:
            _git("fetch", "--quiet", "origin", cwd=folder)
            _git("checkout", "--quiet", "--detach", "origin/HEAD", cwd=folder)
            stamp.touch()
        except LibraryError:
            pass                                    # offline: what was fetched before is still an index
        return folder
    kept.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".fetching-", dir=kept))
    try:
        _git("clone", "--quiet", "--depth", "1", "--no-recurse-submodules", "--", remote, str(scratch / "clone"))
        (scratch / "clone").rename(folder)
    except LibraryError as error:
        raise IndexUnreached(f"the index at {address} could not be reached ({error}). A library can still be "
                             "added by its own address") from None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    stamp.touch()
    return folder
