"""`media library`: voices, sound banks and faces from repositories of their own."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "library", help="add, check and use libraries of voices, sound banks and faces",
        description="A library is a folder, most often a git repository, that holds voices to clone, banks of "
                    "sounds and faces, with a media-library.json that says what is in it. A script goes on naming "
                    "a persona and a bank; a library is where this machine finds them.",
    )
    actions = command.add_subparsers(dest="action", required=True, metavar="action")

    action = actions.add_parser(
        "check", help="read a library and say everything wrong with it",
        description="Read a library's media-library.json and everything it names: every clip is there, has its "
                    "words and its consent and is fit to clone from, every bank reads and every sound it plays is "
                    "there. Needs no database and no model. Exits 1 when the library has problems.")
    action.add_argument("folder", nargs="?", default=".", help="the library's folder (default: this one)")
    action.add_argument("--json", action="store_true", help="print what was found as JSON")
    action.set_defaults(run=run_check)

    action = actions.add_parser(
        "add", help="add a library to this machine, from a folder or a git address",
        description="Add a library. A folder is used where it is; an address is cloned under the engine's home "
                    "and kept at the commit it was added at. `owner/name` is a repository on GitHub. Afterwards "
                    "`@include` finds its banks, films find its faces, and `media library voices` gives its voices "
                    "to the personas here.")
    action.add_argument("source", help="a folder, the address of a git repository, owner/name on GitHub, or the "
                                       "name an index lists it under")
    action.add_argument("--ref", metavar="COMMIT", help="the commit, tag or branch to keep it at")
    action.set_defaults(run=run_add)

    action = actions.add_parser(
        "search", help="look in the indexes for libraries, by any words",
        description="List the libraries the indexes of this machine name, or those every word given is found in: "
                    "by name, summary, tag, license or what they hold (voices, banks, faces). Which indexes are "
                    "read is CONSTRUCTIVE_MEDIA_LIBRARY_INDEX. Add one that is found by its name: media library add NAME")
    action.add_argument("words", nargs="*", help="words to look for; none lists everything")
    action.add_argument("--refresh", action="store_true", help="fetch the indexes again before looking")
    action.add_argument("--json", action="store_true", help="print what was found as JSON")
    action.set_defaults(run=run_search)

    action = actions.add_parser("list", help="the libraries added, and what each holds")
    action.add_argument("--json", action="store_true", help="print them as JSON")
    action.set_defaults(run=run_list)

    action = actions.add_parser(
        "update", help="bring an added library to where its main branch is, or to a commit",
        description="Fetch an added library and move it to --ref, or to where its main branch now is. One that "
                    "has problems there is left where it was. Voices already given are not changed: give them "
                    "again with `media library voices`.")
    action.add_argument("name", help="the library's name")
    action.add_argument("--ref", metavar="COMMIT", help="the commit, tag or branch to bring it to")
    action.set_defaults(run=run_update)

    action = actions.add_parser("remove", help="forget an added library",
                                description="Forget an added library and delete the clone made of it. A folder "
                                            "that was used where it is, is left alone, and so are voices given.")
    action.add_argument("name", help="the library's name")
    action.set_defaults(run=run_remove)

    action = actions.add_parser(
        "voices", help="give the personas here the voices of an added library",
        description="Write a library's voices down against the personas of this machine, adding the personas "
                    "that are not here yet. A persona that already has a voice from somewhere else keeps it "
                    "unless --replace. Needs the database.")
    action.add_argument("name", help="the library's name")
    action.add_argument("--only", metavar="WHO", help="only these personas, with commas between")
    action.add_argument("--replace", action="store_true", help="take the place of voices that came from elsewhere")
    action.set_defaults(run=run_voices)

    action = actions.add_parser(
        "lock", help="write down which libraries are added and at what commit, for a project to keep",
        description="Write media-libraries.json: every library added to this machine, where it is from and the "
                    "commit it is at. Commit that file with a project, and `media library install` gives anyone "
                    "else the same libraries.")
    action.add_argument("file", nargs="?", default=None, help="the file to write (default: media-libraries.json)")
    action.set_defaults(run=run_lock)

    action = actions.add_parser(
        "install", help="add every library a project names, at the commit it names",
        description="Read media-libraries.json and add each library in it at its commit, bringing one that is "
                    "here at another commit to that one.")
    action.add_argument("file", nargs="?", default=None, help="the file to read (default: media-libraries.json)")
    action.set_defaults(run=run_install)


def _holds(library) -> str:
    def count(number: int, what: str) -> str:
        return f"{number} {what}" + ("" if number == 1 else "s")

    return ", ".join((count(len(library.voices), "voice"), count(len(library.banks), "folder") + " of banks",
                      count(len(library.faces), "face")))


def run_check(arguments: argparse.Namespace) -> int | None:
    import json

    from .. import indexes, libraries

    if indexes.is_index(arguments.folder) and not (Path(arguments.folder).expanduser() / libraries.MANIFEST).is_file():
        return _check_index(arguments)
    checked = libraries.check(arguments.folder)
    library = checked.library
    if arguments.json:
        print(json.dumps({
            "library": library and library.name, "problems": list(checked.problems), "notes": list(checked.notes),
            "voices": [{"persona": voice.persona, "emotion": voice.emotion,
                        "reference": voice.reference and str(voice.reference.relative_to(library.folder)),
                        "profile": voice.profile, "consent": voice.consent} for voice in library.voices]
            if library else [],
        }, indent=2, ensure_ascii=False))
        return 1 if checked.problems else None
    for problem in checked.problems:
        print(problem)
    for note in checked.notes:
        print(f"note: {note}")
    if checked.problems:
        count = len(checked.problems)
        print(f"{count} problem{'' if count == 1 else 's'}: this is not a library yet", file=sys.stderr)
        return 1
    print(f"ok: {library.name}, {_holds(library)}")
    return None


def _check_index(arguments: argparse.Namespace) -> int | None:
    import json

    from .. import indexes

    checked = indexes.check(arguments.folder)
    if arguments.json:
        print(json.dumps({"index": checked.name, "problems": list(checked.problems), "notes": list(checked.notes),
                          "libraries": [_listed(one) for one in checked.listed]}, indent=2, ensure_ascii=False))
        return 1 if checked.problems else None
    for problem in checked.problems:
        print(problem)
    for note in checked.notes:
        print(f"note: {note}")
    if checked.problems:
        count = len(checked.problems)
        print(f"{count} problem{'' if count == 1 else 's'}: this is not an index yet", file=sys.stderr)
        return 1
    count = len(checked.listed)
    print(f"ok: the index {checked.name}, {count} librar{'y' if count == 1 else 'ies'}")
    return None


def _listed(one) -> dict:
    return {"name": one.name, "source": one.source, "summary": one.summary, "license": one.license,
            "holds": list(one.holds), "tags": list(one.tags), "index": one.index}


def run_search(arguments: argparse.Namespace) -> None:
    import json

    from .. import indexes, libraries
    from ..settings import Settings

    settings = Settings.from_env()

    def aside(message: str) -> None:
        print(message, file=sys.stderr)

    found = indexes.search(arguments.words, settings, refresh=arguments.refresh, tell=aside)
    if arguments.json:
        print(json.dumps([_listed(one) for one in found], indent=2, ensure_ascii=False))
        return
    if not settings.indexes:
        print("this machine reads no index. Name one in CONSTRUCTIVE_MEDIA_LIBRARY_INDEX, or add a library by "
              "its address")
        return
    if not found:
        print("nothing is listed" + (f" with {' and '.join(arguments.words)} in it" if arguments.words else ""))
        return
    here = libraries.added(settings)
    for one in found:
        about = ", ".join(part for part in (" and ".join(one.holds), one.license) if part)
        print(f"{one.name}" + (f"  ({about})" if about else "") + ("  added" if one.name in here else ""))
        if one.summary:
            print(f"    {one.summary}")
        print(f"    {one.source}")
    print("add one with: media library add NAME")


def run_add(arguments: argparse.Namespace) -> None:
    from .. import libraries

    one, checked = libraries.add(arguments.source, ref=arguments.ref)
    at = f" at {one.commit[:12]}" if one.commit else ""
    print(f"added {one.name}{at}, {_holds(checked.library)}: {one.folder}")
    if checked.library.voices:
        print(f"give its voices to the personas here with: media library voices {one.name}")


def run_list(arguments: argparse.Namespace) -> None:
    import json

    from .. import libraries

    found = libraries.added()
    if arguments.json:
        print(json.dumps({name: {"source": one.source, "folder": str(one.folder), "commit": one.commit,
                                 "linked": one.linked} for name, one in found.items()}, indent=2))
        return
    if not found:
        print("no library has been added. Add one with: media library add <a folder or a git address>")
        return
    for name, one in found.items():
        checked = libraries.check(one.folder, measure=False)
        holds = _holds(checked.library) if checked.library else "which cannot be read: " + checked.problems[0]
        at = one.commit[:12] if one.commit else "no commit"
        print(f"{name}  {at}  {holds}")
        print(f"    {'a folder used where it is: ' if one.linked else 'from '}{one.source}")


def run_update(arguments: argparse.Namespace) -> None:
    from .. import libraries

    one, before = libraries.update(arguments.name, ref=arguments.ref)
    now = one.commit[:12] if one.commit else "no commit"
    print(f"{one.name} is at {now}" + ("" if one.commit != before else ", where it was"))


def run_remove(arguments: argparse.Namespace) -> None:
    from .. import libraries

    one = libraries.remove(arguments.name)
    print(f"forgot {one.name}" + (f"; {one.folder} was left as it is" if one.linked else ""))


def run_voices(arguments: argparse.Namespace) -> None:
    from .. import libraries

    only = [who.strip() for who in (arguments.only or "").split(",") if who.strip()]
    done = libraries.voices(arguments.name, only=only, replace=arguments.replace, tell=print)
    print(f"{len(done['given'])} given a voice" + (f", {len(done['kept'])} kept their own" if done["kept"] else ""))


def run_lock(arguments: argparse.Namespace) -> None:
    from .. import libraries

    file = arguments.file or libraries.LOCK
    found = libraries.lock(file)
    print(f"{len(found)} librar{'y' if len(found) == 1 else 'ies'}: {file}")


def run_install(arguments: argparse.Namespace) -> None:
    from .. import libraries

    done = libraries.install(arguments.file or libraries.LOCK, tell=print)
    print(f"{len(done)} librar{'y' if len(done) == 1 else 'ies'} here")
