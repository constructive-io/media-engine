"""Commands that measure the sound and the movement: loudness, audible, laughter, coordination.

Each writes down what it finds, and `media score` turns that into scores a
recipe can weigh.
"""

from __future__ import annotations

import argparse
from typing import Any

from .analyze import MATCH_HELP


def register(commands: Any) -> None:
    command = commands.add_parser(
        "loudness", help="measure how loud each shot is against what surrounds it",
        description="Give each shot its level, its peak and its lift: how many decibels it sits above "
                    "the minute around it. A shot well above its surroundings is where something "
                    "happened. `media score` makes the lift the score `energy_lift`.",
    )
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--redo", action="store_true", help="measure again shots already measured")
    command.set_defaults(run=run_loudness)

    command = commands.add_parser(
        "audible", help="measure how well each transcribed line can be heard",
        description="Give each line of each transcript its level, the noise under it, how far the speech "
                    "stands above that, and whether it is distorted. `media assemble` takes each "
                    "passage's sound from the cleanest recording measured, and `media score` makes the "
                    "scores `audio_snr`, `undistorted` and `spectral_balance`.",
    )
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--redo", action="store_true", help="measure again lines already measured")
    command.set_defaults(run=run_audible)

    command = commands.add_parser(
        "laughter", help="find where people laugh, from the sound",
        description="Listen to each recording not yet listened to for laughter, then list the surest "
                    "candidates. These are candidates: listen, and say which are right with --yes or "
                    "--no and the start of an id. `media score` makes the score `laughter`: whether "
                    "anyone laughed at a passage, or just after it.",
    )
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--sure", type=float, metavar="0-1",
                         help="the least sure a candidate may be (default 0.7)")
    command.add_argument("--redo", action="store_true",
                         help="listen again to every recording; what you have ruled on is kept")
    command.add_argument("--limit", type=int, default=15, help="how many to list (default %(default)s)")
    ruling = command.add_mutually_exclusive_group()
    ruling.add_argument("--yes", metavar="ID", help="this one is laughter")
    ruling.add_argument("--no", metavar="ID", help="this one is not")
    command.set_defaults(run=run_laughter)

    command = commands.add_parser(
        "coordination", help="measure whether the lit part of each shot moves with the rest of it",
        description="For each shot, follow the movement of what is bright and strongly coloured (beams, "
                    "screens, stage lights) and of everything else, and measure how closely the two "
                    "move together over its best four seconds. `media score` makes it the score "
                    "`coordination`.",
    )
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--redo", action="store_true", help="measure again shots already measured")
    command.set_defaults(run=run_coordination)


def run_loudness(arguments: argparse.Namespace) -> None:
    from ..video import loudness

    done = loudness.measure(arguments.match, redo=arguments.redo)
    for name, count in done:
        print(f"  {count:>5} shots  {name}")
    print(f"{sum(count for _, count in done)} shots measured")


def run_audible(arguments: argparse.Namespace) -> None:
    from ..audio import audible

    done = audible.scan(arguments.project, arguments.match, redo=arguments.redo)
    for name, count in done:
        print(f"  {count:>5} lines  {name}")
    print(f"{sum(count for _, count in done)} lines measured")
    for verdict, count in audible.verdicts(arguments.project):
        print(f"  {count:>5} {verdict}")


def run_laughter(arguments: argparse.Namespace) -> None:
    from ..audio import laughter
    from ..edit import clock

    if arguments.yes or arguments.no:
        name, start, end = laughter.rule(arguments.yes or arguments.no, bool(arguments.yes))
        print(f"{name} {clock(start)}-{clock(end)}: {'laughter' if arguments.yes else 'not laughter'}")
        return
    how = {} if arguments.sure is None else {"min_confidence": arguments.sure}
    done = laughter.scan(arguments.project, arguments.match, redo=arguments.redo, **how)
    for name, count in done:
        print(f"  {count:>5} found  {name}")
    print(f"{len(done)} recordings listened to")
    for laugh_id, name, start, end, sure, confirmed in laughter.found(arguments.project, limit=arguments.limit):
        said = "  yes" if confirmed else ""
        print(f"  {str(laugh_id)[:8]}  {sure:.2f}  {clock(start)}-{clock(end)}  {name}{said}")


def run_coordination(arguments: argparse.Namespace) -> None:
    from ..video import coordination

    done = coordination.measure(arguments.match, redo=arguments.redo)
    for name, count, lit in done:
        print(f"  {count:>5} shots, {lit} with something lit  {name}")
    print(f"{sum(count for _, count, _ in done)} shots measured")
