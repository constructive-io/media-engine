"""Commands for what is in the library: ingest, lineage and outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import derive, ingest


def register(commands: Any) -> None:
    command = commands.add_parser("ingest", help="register a file or a folder of media")
    command.add_argument("path")
    command.add_argument("--project", help="a label that groups these assets")
    command.set_defaults(run=run_ingest)

    command = commands.add_parser("lineage", help="show what a file was made from")
    command.add_argument("path")
    command.set_defaults(run=run_lineage)

    command = commands.add_parser(
        "outputs", help="list every file the engine has made, and find the ones it has no record of",
        description="List every derived file on record: what kind it is, how long, where it is and what it "
                    "was made from. One whose file is gone is still listed, since the record is how to make "
                    "it again. Then look through a folder for pictures and sound that nothing on record "
                    "names: nobody can say what those were made from. Nothing is changed or deleted.",
    )
    command.add_argument("--kind", choices=derive.KINDS, help="list only this kind of file")
    command.add_argument("--in", dest="folder", type=Path, metavar="FOLDER",
                         help="the folder to look through for files with no record (default: the work folder)")
    command.set_defaults(run=run_outputs)


def run_ingest(arguments: argparse.Namespace) -> int:
    results = ingest.ingest(arguments.path, arguments.project)
    for result in results:
        print(f"  {result.state:<12} {result.name}")
    failed = sum(1 for r in results if r.state.startswith(("failed", "unreadable")))
    print(f"{len(results) - failed} of {len(results)} files registered")
    return 1 if failed and failed == len(results) else 0


def run_lineage(arguments: argparse.Namespace) -> int:
    found = derive.lineage(arguments.path)
    if found is None:
        print(f"no record of {arguments.path}: it was not made by a recorded tool")
        return 1
    print(f"{found.kind} made by `{found.tool}` on {found.created_at:%Y-%m-%d %H:%M}")
    for key, value in found.params.items():
        print(f"  {key:<14} {value}")
    print(f"  {'sources':<14} {', '.join(found.sources) or 'none recorded'}")
    return 0


def run_outputs(arguments: argparse.Namespace) -> int:
    from .. import outputs
    from ..edit import clock
    from ..settings import Settings

    settings = Settings.from_env()
    folder = (arguments.folder or settings.work_dir).expanduser()
    if arguments.folder and not folder.is_dir():
        print(f"media: {folder} is not a folder", file=sys.stderr)
        return 1
    made = outputs.made(arguments.kind)
    for one in made:
        cut = one.params.get("timeline")
        print(f"  {one.kind:<8} {clock(one.duration_s) if one.duration_s else '':>8}  {one.path}"
              + (f"  from {', '.join(one.sources)}" if one.sources else "  from nothing on record")
              + (f"; the cut {cut}" if cut else "") + ("" if one.there else "; no longer there"))
    gone = sum(not one.there for one in made)
    print(f"{len(made)} {arguments.kind + ' ' if arguments.kind else 'derived '}file{'' if len(made) == 1 else 's'} "
          "on record" + (f", {gone} no longer there" if gone else ""))
    strays = outputs.unrecorded(folder, settings) if folder.is_dir() else []
    if strays:
        print(f"{len(strays)} file{'' if len(strays) == 1 else 's'} in {folder} with no record of what "
              f"{'it was' if len(strays) == 1 else 'they were'} made from:")
        for file in strays:
            print(f"  {file.stat().st_size / 1_000_000:>8.1f} MB  {file}")
    else:
        print(f"every picture and sound in {folder} is on record" if folder.is_dir()
              else f"{folder} is not there yet: nothing to look through")
    return 0
