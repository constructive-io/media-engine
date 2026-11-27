"""Commands that read `.conv` scripts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def register(commands: Any) -> None:
    command = commands.add_parser(
        "conv", help="check .conv scripts, or print one as the scene it describes",
        description="Read each script and report everything wrong in it: the line, what is wrong and "
                    "how to mend it. With --json, print one script as the scene it describes.",
    )
    command.add_argument("scripts", nargs="+", metavar="script", help="a .conv script")
    command.add_argument("--banks", action="append", default=[], metavar="FOLDER",
                         help="where `@include NAME` finds NAME.sounds; give it again for more folders "
                              "(default: a `banks` folder beside the script, then one here)")
    command.add_argument("--json", action="store_true", help="print the scene as JSON")
    command.add_argument("--compact", action="store_true", help="with --json: on one line")
    command.set_defaults(run=run_conv)


def run_conv(arguments: argparse.Namespace) -> int:
    from .. import conv, libraries

    shared = libraries.banks()
    if arguments.json and len(arguments.scripts) > 1:
        print("media: --json prints one script", file=sys.stderr)
        return 2
    failed = 0
    for script in arguments.scripts:
        if not os.path.isfile(script):
            print(f"{script}: no such file\n", file=sys.stderr)
            failed += 1
            continue
        try:
            scene = conv.load(script, banks=[*conv.bank_folders(script, arguments.banks), *shared])
        except conv.ConvError as error:
            print(conv.report(error.problems, script) + "\n", file=sys.stderr)
            failed += 1
            continue
        if arguments.json:
            print(json.dumps(scene, ensure_ascii=False, indent=None if arguments.compact else 2,
                             separators=(",", ":") if arguments.compact else None))
        else:
            print(f"ok  {script}")
    if len(arguments.scripts) > 1:
        print(f"{len(arguments.scripts) - failed} ok, {failed} with problems")
    return 1 if failed else 0
