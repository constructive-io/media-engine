"""Commands that look at video: shots, frames, signatures, detect."""

from __future__ import annotations

import argparse
from typing import Any

from ..video import cuts

MATCH_HELP = "only assets whose path matches this regular expression"


def register(commands: Any) -> None:
    command = commands.add_parser("shots", help="split each video into shots")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--threshold", type=float, default=cuts.DEFAULT_THRESHOLD,
                         help="how different two frames must be to call it a cut (default %(default)s)")
    command.set_defaults(run=run_shots)

    command = commands.add_parser("frames", help="save and measure sample frames from each shot")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--per-shot", type=int, default=3, choices=range(1, 8), metavar="1-7")
    command.set_defaults(run=run_frames)

    command = commands.add_parser("signatures", help="give each shot a colour and motion signature")
    command.add_argument("--match", help=MATCH_HELP)
    command.set_defaults(run=run_signatures)

    command = commands.add_parser("detect", help="find objects and people in the sampled frames")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--confidence", type=float, default=0.35,
                         help="the least sure a detection may be (default %(default)s)")
    command.add_argument("--adapter", help="the detection adapter to use")
    command.set_defaults(run=run_detect)


def run_shots(arguments: argparse.Namespace) -> None:
    from ..video import analyze

    done = analyze.shots(arguments.match, arguments.threshold)
    for name, count in done:
        print(f"  {count:>5} shots  {name}")
    print(f"{len(done)} videos split into shots")


def run_frames(arguments: argparse.Namespace) -> None:
    from ..video import analyze

    done = analyze.frames(arguments.match, arguments.per_shot)
    for name, count in done:
        print(f"  {count:>5} frames  {name}")
    print(f"{sum(count for _, count in done)} frames saved")


def run_signatures(arguments: argparse.Namespace) -> None:
    from ..video import analyze

    print(f"{analyze.signatures(arguments.match)} shots signed")


def run_detect(arguments: argparse.Namespace) -> None:
    from ..detection import detector
    from ..video import analyze

    finder = detector(arguments.adapter)
    count = analyze.detect(arguments.match, arguments.confidence, detector=finder)
    print(f"{count} frames searched with {finder.name} ({finder.model})")
