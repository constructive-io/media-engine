"""Commands for what is in the library: ingest and lineage."""

from __future__ import annotations

import argparse
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
