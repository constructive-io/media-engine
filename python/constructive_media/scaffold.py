"""Boilerplates: a project started from a folder of files with blanks in them.

A boilerplate repository is a folder, most often a git repository, that holds
one template or several. A template is a folder with a `.boilerplate.json`:

    .boilerplates.json                  {"dir": "media", "aliases": {"deck": "module"},
                                         "first": {"template": "module", "name": "first-talk"}}
    media/workspace/.boilerplate.json   {"type": "workspace", "questions": [...]}
    media/module/.boilerplate.json      {"type": "module", "requiresWorkspace": "pnpm", "questions": [...]}
    media/module/talk.conv              ... ____title____ ...

A blank is a word between four underscores on each side, `____title____`, in
a file or in the name of a file or folder. A question fills a blank:

    {"name": "____title____", "message": "The title on the first screen", "default": "A short talk"}

This is the format `pgpm init` reads, so one repository serves both: what is
written here is what that command does with the same files, read from its
source. Three things are this engine's own. A file that is not text is copied
as it is, where the other would read it as text. `.boilerplates.json` may give
other names to a template, as `aliases`. And it may say what a new workspace
starts with, as `first`: a template and a name for what it makes. The other
command reads neither, and makes the workspace alone.

Nothing in a template is run. Its files are copied, its blanks filled, and
its `.motd` printed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .settings import Settings

ROOT = ".boilerplates.json"
TEMPLATE = ".boilerplate.json"
#: Never copied: what describes a template is not part of what it makes.
_APART = {ROOT, TEMPLATE, ".boilerplate.js", ".git"}
_BLANK = re.compile(r"____([A-Za-z_][A-Za-z0-9_]*)____")
_SHORT = re.compile(r"[A-Za-z0-9][\w.-]*/(?!\.{1,2}$)[\w.-]+")     # owner/name, and never a path such as ../name
_FETCHED = ("https://", "ssh://", "git@", "file://")
#: The file that says a folder is a workspace, by the kind of workspace a template may ask for.
_WORKSPACES = {
    "pgpm": ("pgpm.json", "pgpm.config.js"),
    "pnpm": ("pnpm-workspace.yaml",),
    "lerna": ("lerna.json",),
}
#: Licenses a template can offer. The first five are written as a LICENSE file; the text of the others is long
#: and is yours to put there.
LICENSES = ("MIT", "ISC", "BSD-3-CLAUSE", "UNLICENSE", "CLOSED", "APACHE-2.0", "CONSTRUCTIVE", "GPL-3.0", "MPL-2.0")
#: What a workspace's own name is asked as, and what anything else's is.
_NAMED = {"workspace": "name"}


class ScaffoldError(Exception):
    """A boilerplate that could not be found, read or written out."""


@dataclass(frozen=True)
class Question:
    """What a template asks, and how it can be answered without asking."""

    name: str
    message: str
    kind: str = "text"
    default: Any = None
    default_from: str | None = None
    set_from: str | None = None
    required: bool = False
    options: tuple[str, ...] = ()
    use_default: bool = False


@dataclass(frozen=True)
class Template:
    """One template of a boilerplate repository."""

    name: str                       # its folder within the repository, such as media/module
    folder: Path
    kind: str                       # workspace, module, or whatever else it calls itself
    requires: str | None            # the kind of workspace it goes into, or None when it goes anywhere
    questions: tuple[Question, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Made:
    """What starting a project made."""

    folder: Path
    template: Template
    files: tuple[Path, ...]
    answers: Mapping[str, Any]
    said: str | None = None                             # the template's `.motd`, with its blanks filled
    notes: tuple[str, ...] = field(default_factory=tuple)


# Finding a boilerplate repository and the templates in it.

def fetch(repo: str, settings: Settings | None = None, *, branch: str | None = None, refresh: bool = False) -> Path:
    """The folder a boilerplate repository is in on this machine.

    A folder is used where it is. `owner/name` is a repository on GitHub, and an address is cloned under the
    engine's home, looked at again once a day or when asked.
    """
    from .libraries import LibraryError, kept_clone

    if repo.startswith((".", "/", "~")):              # a folder is said as a path, and a path starts with one of these
        local = Path(repo).expanduser()
        if not local.is_dir():
            raise ScaffoldError(f"{repo}: no such folder of boilerplates")
        return local.resolve()
    not_one = ScaffoldError(f"`{repo}` is not where boilerplates are: give a folder as a path (./there), "
                            "owner/name on GitHub, or an address that starts https://, ssh:// or git@")
    if repo.startswith("-") or any(mark in repo for mark in "\n\r\0 ") or "::" in repo:
        raise not_one
    if branch is not None and (branch.startswith("-") or not re.fullmatch(r"[\w./-]+", branch)):
        raise ScaffoldError(f"`{branch}` is not the name of a branch")
    if repo.startswith(_FETCHED):
        remote = repo
    elif _SHORT.fullmatch(repo):
        remote = f"https://github.com/{repo}.git"
    else:
        raise not_one
    settings = settings or Settings.from_env()
    try:
        return kept_clone(repo + (f"#{branch}" if branch else ""), remote, settings.home / "boilerplates",
                          refresh=refresh, branch=branch)
    except LibraryError as error:
        raise ScaffoldError(f"the boilerplates at {repo} could not be reached ({error})") from None


def _json(path: Path) -> dict:
    try:
        read = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ScaffoldError(f"{path} cannot be read as JSON: {error}") from None
    if not isinstance(read, dict):
        raise ScaffoldError(f"{path} should hold an object, in braces")
    return read


def _root(root: Path) -> tuple[str | None, dict[str, str]]:
    """What a repository's `.boilerplates.json` says: the folder its templates are in, and other names for them."""
    if not (root / ROOT).is_file():
        return None, {}
    said = _json(root / ROOT)
    within = said.get("dir")
    aliases = said.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(to, str) for to in aliases.values()):
        raise ScaffoldError(f'{root / ROOT}: "aliases" should give a template for each other name')
    return (within if isinstance(within, str) and within not in ("", ".") else None), aliases


def first(root: str | Path) -> tuple[str, str] | None:
    """What a new workspace from these boilerplates starts with: a template, and the name of what it makes."""
    root = Path(root)
    said = _json(root / ROOT).get("first") if (root / ROOT).is_file() else None
    if said is None:
        return None
    if (not isinstance(said, dict) or not isinstance(said.get("template"), str)
            or not isinstance(said.get("name"), str) or not slug(said["name"])):
        raise ScaffoldError(f'{root / ROOT}: "first" names a template and gives a name: '
                            '{"template": "module", "name": "first-talk"}')
    return said["template"], said["name"]


def _stripped(name: str) -> str:
    """`____title____` and `__title__` are both the blank called `title`."""
    for marks in ("____", "__"):
        if name.startswith(marks) and name.endswith(marks) and len(name) > 2 * len(marks):
            return name[len(marks):-len(marks)]
    return name


def _read(root: Path, folder: Path, aliases: Mapping[str, str]) -> Template:
    said = _json(folder / TEMPLATE)
    asked = said.get("questions", [])
    if not isinstance(asked, list):
        raise ScaffoldError(f'{folder / TEMPLATE}: "questions" should be a list')
    questions = []
    for one in asked:
        if not isinstance(one, dict) or not isinstance(one.get("name"), str):
            raise ScaffoldError(f"{folder / TEMPLATE}: every question has a \"name\", the blank it fills")
        options = one.get("options", [])
        if one.get("optionsFrom") == "licenses":
            options = list(LICENSES)
        questions.append(Question(
            name=_stripped(one["name"]),
            message=str(one.get("message") or f"Enter value for {_stripped(one['name'])}"),
            kind=str(one.get("type") or "text"),
            default=one.get("default"),
            default_from=one.get("defaultFrom") if isinstance(one.get("defaultFrom"), str) else None,
            set_from=one.get("setFrom") if isinstance(one.get("setFrom"), str) else None,
            required=bool(one.get("required")),
            options=tuple(str(option) for option in options) if isinstance(options, list) else (),
            use_default=bool(one.get("useDefault")),
        ))
    needs = said.get("requiresWorkspace", "pgpm")
    name = folder.relative_to(root).as_posix()
    last = name.rsplit("/", 1)[-1]
    return Template(
        name=name, folder=folder, kind=str(said.get("type") or "module"),
        requires=needs if isinstance(needs, str) and needs else None,
        questions=tuple(questions),
        aliases=tuple(sorted(other for other, to in aliases.items() if to in (name, last))),
    )


def templates(root: str | Path) -> list[Template]:
    """Every template in a boilerplate repository."""
    root = Path(root)
    _, aliases = _root(root)
    found = []
    for described in sorted(root.rglob(TEMPLATE)):
        parts = described.relative_to(root).parts
        if ".git" in parts or "node_modules" in parts or len(parts) < 2:
            continue
        found.append(_read(root, described.parent, aliases))
    return found


def find(root: str | Path, name: str, *, variant: str | None = None) -> Template:
    """The template a name means: a folder of the repository, one in the folder it keeps its templates in, or the
    only template whose folder is called that."""
    root = Path(root)
    within, aliases = _root(root)
    name = aliases.get(name, name).strip("/")
    if variant is None and "/" in name:
        ahead, rest = name.split("/", 1)
        if not (root / name / TEMPLATE).is_file() and (root / ahead / rest / TEMPLATE).is_file():
            variant, name = ahead, rest
    tried = [root / variant / name] if variant else [root / name] + ([root / within / name] if within else [])
    for folder in tried:
        if (folder / TEMPLATE).is_file() and _inside(folder, root):
            return _read(root, folder, aliases)
    every = templates(root)
    if not variant:
        named = [one for one in every if one.name.rsplit("/", 1)[-1] == name]
        if len(named) == 1:
            return named[0]
    there = ", ".join(sorted({one.name.rsplit("/", 1)[-1] for one in every} | set(aliases))) or "none"
    raise ScaffoldError(f"there is no template called `{name}` here. There is: {there}")


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


# Answers.

def blanks(template: Template) -> list[str]:
    """Every blank in a template's files and their names, in the order they are met."""
    found: dict[str, None] = {}
    for path in _files(template.folder):
        for part in path.relative_to(template.folder).parts:
            for name in _BLANK.findall(part):
                found.setdefault(name)
        text = _text(path)
        if text is not None:
            for name in _BLANK.findall(text):
                found.setdefault(name)
    return list(found)


def _files(folder: Path) -> list[Path]:
    """A template's files. A link is not one: it could point at anything on the machine it is copied on."""
    return sorted(path for path in folder.rglob("*")
                  if path.is_file() and not path.is_symlink() and _inside(path, folder)
                  and not set(path.relative_to(folder).parts) & _APART)


def _text(path: Path) -> str | None:
    """A file's text, or None when it is not text: a picture or a sound is copied as it is."""
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def workspace(where: str | Path, kind: str | None = None) -> tuple[Path, str] | None:
    """The workspace `where` is in, and its kind, looking upwards. `kind` asks for one kind only."""
    where = Path(where).resolve()
    for folder in (where, *where.parents):
        for one, marks in _WORKSPACES.items():
            if kind in (None, one) and any((folder / mark).is_file() for mark in marks):
                return folder, one
        if kind in (None, "npm") and (folder / "package.json").is_file():
            try:
                if "workspaces" in json.loads((folder / "package.json").read_text(encoding="utf-8")):
                    return folder, "npm"
            except (OSError, ValueError):
                pass
    return None


def _git_says(key: str) -> str | None:
    try:
        done = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    except OSError:
        return None
    return done.stdout.strip() or None


def _package(where: Path) -> dict:
    """The nearest package.json at or above `where`: what a workspace says of itself."""
    for folder in (where, *where.parents):
        if (folder / "package.json").is_file():
            try:
                read = json.loads((folder / "package.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return read if isinstance(read, dict) else {}
    return {}


def _author(package: dict) -> tuple[str | None, str | None]:
    author = package.get("author")
    if isinstance(author, dict):
        return author.get("name") or None, author.get("email") or None
    if isinstance(author, str) and author.strip():
        named = re.fullmatch(r"\s*([^<(]*?)\s*(?:<([^>]*)>)?\s*(?:\([^)]*\))?\s*", author)
        if named:
            return named.group(1) or None, named.group(2) or None
    return None, None


def _repository(package: dict) -> tuple[str | None, str | None]:
    """The owner and the name in a package's repository address."""
    repository = package.get("repository")
    address = repository.get("url") if isinstance(repository, dict) else repository
    if not isinstance(address, str):
        return None, None
    found = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", address)
    return (found.group(1), found.group(2)) if found else (None, None)


def resolvers(where: Path, *, dirname: str | None = None) -> dict[str, Callable[[], Any]]:
    """Where an answer can come from without asking: git, the day, and the workspace `where` is in."""
    now = datetime.now()

    def of_workspace(key: str) -> Callable[[], Any]:
        def get() -> Any:
            package = _package(where)
            owner, name = _repository(package)
            author, email = _author(package)
            return {
                "name": package.get("name"), "license": package.get("license"),
                "repo.name": name, "repo.organization": owner, "organization.name": owner,
                "author": author, "author.name": author, "author.email": email,
            }.get(key)
        return get

    found: dict[str, Callable[[], Any]] = {
        "git.user.name": lambda: _git_says("user.name"),
        "git.user.email": lambda: _git_says("user.email"),
        "date.year": lambda: str(now.year),
        "date.month": lambda: f"{now.month:02d}",
        "date.day": lambda: f"{now.day:02d}",
        "date.now": lambda: now.isoformat(timespec="seconds"),
        "date.iso": lambda: now.date().isoformat(),
        "date.timestamp": lambda: str(int(now.timestamp())),
        "workspace.dirname": lambda: dirname,
    }
    for key in ("name", "license", "repo.name", "repo.organization", "organization.name", "author", "author.name",
                "author.email"):
        found[f"workspace.{key}"] = of_workspace(key)
    return found


def answer(template: Template, given: Mapping[str, Any], *, where: Path, dirname: str | None = None,
           ask: Callable[[Question, Any], Any] | None = None,
           sources: Mapping[str, Callable[[], Any]] | None = None) -> dict[str, Any]:
    """An answer for every blank of a template.

    What was `given` stands. Then a question's `setFrom` answers it without asking, when its source has an answer.
    Otherwise `ask` is asked, with the default to offer; with nobody to ask, the default is taken, and a required
    question with no default is an error that names the flag to give.
    """
    sources = dict(sources) if sources is not None else resolvers(where, dirname=dirname)

    def source(name: str | None) -> Any:
        if not name or name not in sources:
            return None
        found = sources[name]()
        return found if found not in (None, "") else None

    answers = dict(given)
    asked = {question.name: question for question in template.questions}
    for name in blanks(template):                   # a blank nothing asks about is asked about
        if name not in answers:
            asked.setdefault(name, Question(name=name, message=f"Enter value for {name}", required=True))
    missing = []
    for question in asked.values():
        said = answers.get(question.name)
        if said in (None, ""):
            said = source(question.set_from)
        if said is None:
            default = source(question.default_from)
            if default is None:
                default = question.default
            if ask is not None and not (question.use_default and default is not None):
                said = ask(question, default)
            if said in (None, ""):
                said = default
        if said in (None, ""):
            answers.pop(question.name, None)
            if question.required:
                missing.append(question)
            continue
        answers[question.name] = _as_asked(question, said)
    if missing:
        raise ScaffoldError("this template asks for " + ", ".join(
            f"--{question.name} ({question.message.rstrip(':? ')})" for question in missing))
    return answers


def _as_asked(question: Question, said: Any) -> Any:
    """An answer as the kind of question it answers: one of its options, yes or no, or a number."""
    if question.options:
        folded = {option.lower(): option for option in question.options}
        if str(said).lower() not in folded:
            raise ScaffoldError(f"--{question.name} is one of {', '.join(question.options)}, not `{said}`")
        return folded[str(said).lower()]
    if question.kind in ("confirm", "boolean") and not isinstance(said, bool):
        if str(said).strip().lower() in ("y", "yes", "true", "1"):
            return True
        if str(said).strip().lower() in ("n", "no", "false", "0"):
            return False
        raise ScaffoldError(f"--{question.name} is yes or no, not `{said}`")
    if question.kind == "number" and not isinstance(said, (int, float)):
        try:
            return float(said) if "." in str(said) else int(said)
        except ValueError:
            raise ScaffoldError(f"--{question.name} is a number, not `{said}`") from None
    return said


def nobody_to_ask() -> bool:
    """Whether a question would wait for ever: a machine is running this, or nothing is typed into it."""
    import sys

    return os.environ.get("CI") == "true" or not sys.stdin.isatty()


# Writing a project out.

def slug(name: str) -> str:
    """A name as a folder is called: lower case, hyphens for its spaces, and nothing but letters, numbers, `_`
    and `-`."""
    spaced = re.sub(r"\s+", "-", str(name).lower().strip()).replace("&", "-and-")
    return re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9_-]+", "", spaced)).strip("-")


def _filled(text: str, answers: Mapping[str, Any]) -> str:
    def fill(found: re.Match) -> str:
        value = answers.get(found.group(1))
        if value is None:
            return found.group(0)                   # a blank with no answer is left as it is
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    return _BLANK.sub(fill, text)


def _license(answers: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The LICENSE a project asked for, and what to say when its text is not held here."""
    chosen = answers.get("LICENSE") or answers.get("license")
    if not isinstance(chosen, str) or not chosen.strip():
        return None, None
    name = chosen.strip().upper()
    if name not in _TEXTS:
        if name in LICENSES:
            return None, f"the text of {chosen} is long and is not written for you: put it in a LICENSE file"
        return None, f"`{chosen}` is not a license known here, so no LICENSE was written"
    author = next((str(answers[key]) for key in ("USERFULLNAME", "AUTHOR", "AUTHORFULLNAME", "USERNAME", "fullName",
                                                 "author", "authorFullName", "userName") if answers.get(key)), "")
    email = next((str(answers[key]) for key in ("USEREMAIL", "EMAIL", "email", "userEmail") if answers.get(key)), "")
    holder = (author or "Unknown Author") + (f" <{email}>" if email else "")
    return _TEXTS[name].format(year=datetime.now().year, holder=holder), None


def write(template: Template, into: str | Path, answers: Mapping[str, Any]) -> Made:
    """Copy a template's files into `into`, with every blank that has an answer filled."""
    into = Path(into)
    if into.exists() and (not into.is_dir() or any(one.name != ".git" for one in into.iterdir())):
        raise ScaffoldError(f"{into} is there already and is not empty. Nothing was written")
    written: list[Path] = []
    said = None
    for path in _files(template.folder):
        parts = [_filled(part, answers) for part in path.relative_to(template.folder).parts]
        if any(part in ("", ".", "..") or "/" in part or "\\" in part for part in parts):
            raise ScaffoldError(f"an answer would put `{path.relative_to(template.folder)}` outside the project")
        text = _text(path)
        if parts == [".motd"]:
            said = _filled(text or "", answers).strip("\n") or None
            continue
        target = into.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copyfile(path, target)
        else:
            target.write_text(_filled(text, answers), encoding="utf-8")
        shutil.copymode(path, target)
        written.append(target)
    notes = []
    text, note = _license(answers)
    if text is not None:
        (into / "LICENSE").write_text(text, encoding="utf-8")
        written.append(into / "LICENSE")
    if note:
        notes.append(note)
    return Made(into, template, tuple(written), dict(answers), said, tuple(notes))


def start(repo: str, name: str, called: str | None = None, *, where: str | Path = ".",
          given: Mapping[str, Any] | None = None, variant: str | None = None, branch: str | None = None,
          refresh: bool = False, settings: Settings | None = None,
          ask: Callable[[Question, Any], Any] | None = None,
          sources: Mapping[str, Callable[[], Any]] | None = None) -> Made:
    """Start a project: the template `name` of the boilerplates at `repo`, written out and called `called`.

    A workspace is a folder of its own under `where`, or `where` itself when that is an empty folder of the same
    name. Anything else goes into `packages/` when `where` is the top of a workspace of the kind the template asks
    for, and beside `where` otherwise.
    """
    where = Path(where).expanduser().resolve()
    if not where.is_dir():
        raise ScaffoldError(f"{where}: no such folder to start a project in")
    template = find(fetch(repo, settings, branch=branch, refresh=refresh), name, variant=variant)
    given = dict(given or {})
    is_workspace = template.kind == "workspace"
    key = _NAMED.get(template.kind, "moduleName")
    called = called or given.get(key)
    if not called and ask is not None:
        called = ask(Question(name=key, message=f"Enter {'workspace' if is_workspace else 'module'} name",
                              required=True), None)
    if not called or not slug(str(called)):
        raise ScaffoldError(f"give it a name: me init {name} NAME")
    folder = slug(str(called))
    notes: list[str] = []
    if is_workspace:
        in_place = where.name == folder and all(one.name == ".git" for one in where.iterdir())
        into = where if in_place else where / folder
        given.update(name=str(called), workspaceName=str(called))
        answers = answer(template, given, where=where, dirname=folder, ask=ask, sources=sources)
    else:
        found = workspace(where, template.requires) if template.requires else None
        into = where / "packages" / folder if found and found[0] == where else where / folder
        if template.requires and not found:
            notes.append(f"this is not inside a {template.requires} workspace, so it was made where you are. "
                         "`me init workspace NAME` makes a workspace")
        given["moduleName"] = folder
        given.setdefault("packageIdentifier", folder)
        answers = answer(template, given, where=where, ask=ask, sources=sources)
    made = write(template, into, answers)
    return Made(made.folder, template, made.files, made.answers, made.said, (*notes, *made.notes))


_TEXTS = {
    "MIT": """The MIT License (MIT)

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
    "ISC": """ISC License

Copyright (c) {year} {holder}

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
""",
    "BSD-3-CLAUSE": """BSD 3-Clause License

Copyright (c) {year}, {holder}

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
""",
    "UNLICENSE": """This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or distribute
this software, either in source code form or as a compiled binary, for any
purpose, commercial or non-commercial, and by any means.

In jurisdictions that recognize copyright laws, the author or authors of this
software dedicate any and all copyright interest in the software to the public
domain. We make this dedication for the benefit of the public at large and to
the detriment of our heirs and successors. We intend this dedication to be an
overt act of relinquishment in perpetuity of all present and future rights to
this software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org>
""",
    "CLOSED": """Copyright (c) {year} {holder}

All Rights Reserved.

This software and associated documentation files (the "Software") are the
exclusive property of the copyright holder. No part of the Software may be
reproduced, distributed, modified, or transmitted in any form or by any means,
including photocopying, recording, or other electronic or mechanical methods,
without the prior written permission of the copyright holder.

Unauthorized copying, modification, distribution, or use of this Software,
via any medium, is strictly prohibited.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
}
