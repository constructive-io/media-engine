"""`media init`: start a project from a boilerplate."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

#: What `media init` is told with a flag of its own; any other `--name value` answers a question of the template.
_OWN = ("--repo", "--dir", "--template", "-t", "--from-branch", "--refresh", "--no-cache", "--list", "--cwd",
        "--no-tty", "--bare", "--json", "--help", "-h")
_TAKE_NO_VALUE = ("--refresh", "--no-cache", "--list", "--no-tty", "--bare", "--json", "--help", "-h")


def register(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "init", help="start a project from a boilerplate: a workspace, or a deck inside one",
        description="Start a project from a template: a folder of files with blanks in them, kept in a "
                    "repository of boilerplates. `media init workspace NAME` makes a workspace, with a first deck "
                    "in it when the boilerplates say so. Inside a workspace, `media init deck NAME` adds another to "
                    "its packages. A template's questions are asked, or answered with a flag each: "
                    "--title \"On tides\". Nothing in a template is run. The same boilerplates work with "
                    "`pgpm init --repo`.",
        epilog="Which boilerplates are read is --repo, or CONSTRUCTIVE_MEDIA_BOILERPLATES. `media init --list` says "
               "what templates they hold and what each asks.")
    command.add_argument("path", nargs="?", metavar="TEMPLATE",
                         help="the template: `workspace`, `module`, or another name the boilerplates give one "
                              "(default: module)")
    command.add_argument("name", nargs="?", metavar="NAME", help="what to call what is made; its folder is named for it")
    command.add_argument("--repo", metavar="WHERE",
                         help="the boilerplates: a folder (./there), owner/name on GitHub, or a git address")
    command.add_argument("--template", "-t", metavar="TEMPLATE", help="the template, said as a flag")
    command.add_argument("--dir", metavar="FOLDER", help="the folder of the boilerplates to look for the template in")
    command.add_argument("--from-branch", metavar="BRANCH", help="the branch of the boilerplates to read")
    command.add_argument("--refresh", "--no-cache", action="store_true",
                         help="fetch the boilerplates again; they are otherwise kept for a day")
    command.add_argument("--cwd", metavar="FOLDER", default=".", help="where to start it (default: here)")
    command.add_argument("--no-tty", action="store_true",
                         help="ask nothing: take each question's default, and say which flags are missing")
    command.add_argument("--bare", action="store_true", help="a workspace alone, without what it would start with")
    command.add_argument("--list", action="store_true", help="say what templates the boilerplates hold")
    command.add_argument("--json", action="store_true", help="print what was made, or listed, as JSON")
    command.set_defaults(run=run_init, answers={})


def split(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Take a template's answers out of what was typed: every `--name value` that is not a flag of `media init`."""
    kept: list[str] = []
    answers: dict[str, Any] = {}
    at = 0
    while at < len(argv):
        word = argv[at]
        at += 1
        if word == "--":
            kept.extend(argv[at - 1:])
            break
        flag = word.split("=", 1)[0]
        if not word.startswith("--") or flag in _OWN:
            kept.append(word)
            if flag in _OWN and flag not in _TAKE_NO_VALUE and "=" not in word and at < len(argv):
                kept.append(argv[at])
                at += 1
            continue
        name = re.sub(r"-+([A-Za-z0-9])", lambda found: found.group(1).upper(), flag[2:])
        if "=" in word:
            answers[name] = word.split("=", 1)[1]
        elif at < len(argv) and not argv[at].startswith("--"):
            answers[name] = argv[at]
            at += 1
        else:
            answers[name] = True
    return kept, answers


def _ask(question, default):
    """Ask one question at the keyboard. An empty line takes the default."""
    offered = "" if default in (None, "") else f" [{default}]"
    if question.options:
        print(f"{question.message}:")
        for number, option in enumerate(question.options, start=1):
            print(f"  {number}. {option}")
        said = input(f"a number or a name{offered}: ").strip()
        if said.isdigit() and 1 <= int(said) <= len(question.options):
            return question.options[int(said) - 1]
        return said
    if question.kind in ("confirm", "boolean"):
        return input(f"{question.message.rstrip('?: ')}? (y/n){offered}: ").strip()
    return input(f"{question.message.rstrip(': ')}{offered}: ").strip()


def run_init(arguments: argparse.Namespace) -> int:
    import json

    from .. import scaffold
    from ..settings import Settings

    settings = Settings.from_env()
    repo = arguments.repo or settings.boilerplates
    if arguments.path and arguments.template:
        raise scaffold.ScaffoldError("the template is given once: as the first word, or with --template")
    root = scaffold.fetch(repo, settings, branch=arguments.from_branch, refresh=arguments.refresh)

    if arguments.list:
        found = scaffold.templates(root)
        if arguments.json:
            print(json.dumps([{
                "template": one.name, "kind": one.kind, "aliases": list(one.aliases), "goes_into": one.requires,
                "asks": [{"name": question.name, "message": question.message, "default": question.default,
                          "options": list(question.options)} for question in one.questions],
            } for one in found], indent=2))
            return 0
        if not found:
            print(f"{repo} holds no templates: a template is a folder with a .boilerplate.json")
            return 1
        for one in found:
            also = f" (also: {', '.join(one.aliases)})" if one.aliases else ""
            where = {"workspace": "a workspace of its own"}.get(
                one.kind, f"into a {one.requires} workspace" if one.requires else "anywhere")
            print(f"{one.name}{also}: {where}")
            for question in one.questions:
                default = "" if question.default in (None, "") else f" [{question.default}]"
                print(f"  --{question.name}  {question.message.rstrip(':? ')}{default}")
        return 0

    ask = None if arguments.no_tty or scaffold.nobody_to_ask() else _ask
    name = arguments.path or arguments.template or "module"
    made = [scaffold.start(str(root), name, arguments.name, where=arguments.cwd, given=arguments.answers,
                           variant=arguments.dir, settings=settings, ask=ask)]
    starts_with = None if arguments.bare or made[0].template.kind != "workspace" else scaffold.first(root)
    if starts_with is not None:
        # What a workspace starts with takes its defaults, and the answers given that it also asks for.
        asked = {question.name for question in scaffold.find(root, starts_with[0]).questions}
        passed = {key: value for key, value in arguments.answers.items() if key in asked}
        made.append(scaffold.start(str(root), starts_with[0], starts_with[1], where=made[0].folder, given=passed,
                                   settings=settings))

    if arguments.json:
        print(json.dumps([{
            "folder": str(one.folder), "template": one.template.name, "kind": one.template.kind,
            "files": [path.relative_to(one.folder).as_posix() for path in one.files], "notes": list(one.notes),
        } for one in made], indent=2))
        return 0
    here = Path(arguments.cwd).expanduser().resolve()
    for one in made:
        try:
            shown = one.folder.relative_to(here).as_posix() or "."
        except ValueError:
            shown = str(one.folder)
        print(f"{shown}: {len(one.files)} files, from {one.template.name}")
        for note in one.notes:
            print(f"  note: {note}", file=sys.stderr)
    said = made[0].said
    if said:
        print()
        print(said)
    return 0
