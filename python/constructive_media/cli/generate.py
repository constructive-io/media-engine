"""Commands for pictures and video a model makes: ask for one, and see what has been asked for."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def register(commands: Any) -> None:
    from ..generate import MEDIUMS, STATUSES

    command = commands.add_parser(
        "generate", help="make a picture or a piece of video from a description",
        description="Hand a description to the model that fills the `image` or the `video` slot, and put what "
                    "it makes on record as generated: the words, the model, the settings and the seed. The "
                    "same request is made once: asked again, this prints the file it has. --again makes "
                    "another. What a maker that asks a service was handed is brought in by `media collect`.",
    )
    command.add_argument("medium", choices=tuple(MEDIUMS), help="what to make")
    command.add_argument("prompt", help="what it should show, in words")
    command.add_argument("--from", dest="image", type=Path, metavar="PICTURE", help="a picture to start from")
    command.add_argument("--size", type=_size, metavar="WxH", help="how wide and how tall, such as 1280x720")
    command.add_argument("--seconds", type=float, metavar="S", help="how long a piece of video is to run")
    command.add_argument("--steps", type=int, metavar="N",
                         help="how many passes the model makes: more is slower, and up to a point better")
    command.add_argument("--seed", type=int, metavar="N", help="the same request with the same seed is the same picture")
    command.add_argument("--out", type=Path, metavar="FILE",
                         help="where to write it (default: the work folder, under generated/)")
    command.add_argument("--adapter", metavar="NAME", help="which maker to ask, in place of the configured one")
    command.add_argument("--project", help="a label that groups it with other assets")
    command.add_argument("--again", action="store_true",
                         help="make another: with a new seed, or in place of the first when --seed is given")
    command.add_argument("--max-usd", type=float, metavar="X", help="refuse a request its maker prices above this")
    command.add_argument("--dry-run", action="store_true", help="show what would be made and make nothing")
    command.set_defaults(run=run_generate)

    command = commands.add_parser(
        "generations", help="list what models have been asked to make, and what it came to",
        description="List every request for a picture or a piece of video, the latest first: how it came "
                    "out, the model, the file and the words. --spend adds them up by model instead.",
    )
    command.add_argument("--medium", choices=tuple(MEDIUMS), help="list only this")
    command.add_argument("--status", choices=STATUSES, help="list only requests that stand here")
    command.add_argument("--limit", type=int, default=20, metavar="N", help="how many to list (default %(default)s)")
    command.add_argument("--spend", action="store_true", help="add up the requests by model, in place of listing them")
    command.set_defaults(run=run_generations)

    command = commands.add_parser(
        "collect", help="bring in what a service has finished making",
        description="Ask every service that was handed a request whether it has finished. What is finished "
                    "is written where `media generate` said it would go and put on record; what failed is "
                    "marked failed, with the reason.",
    )
    command.set_defaults(run=run_collect)

    command = commands.add_parser(
        "synthetic", help="list the clips of a cut that nobody filmed",
        description="List every clip whose picture or sound a model made, in one cut or in every cut. The "
                    "question to ask before a cut is shown as a record of what happened. Returns 1 when "
                    "there are any.",
    )
    command.add_argument("timeline", nargs="?", help="the cut's name (default: every cut)")
    command.set_defaults(run=run_synthetic)


def _size(said: str) -> tuple[int, int]:
    wide, _, tall = said.lower().partition("x")
    if not (wide.isdigit() and tall.isdigit() and int(wide) and int(tall)):
        raise argparse.ArgumentTypeError(f"{said!r} is not a size; give a width and a height, such as 1280x720")
    return int(wide), int(tall)


def _short(words: str, most: int = 72) -> str:
    return words if len(words) <= most else words[: most - 1].rstrip() + "…"


def run_generate(arguments: argparse.Namespace) -> None:
    from .. import generate

    width, height = arguments.size or (None, None)
    done = generate.make(
        generate.Request(
            arguments.medium, arguments.prompt, width=width, height=height, seconds=arguments.seconds,
            steps=arguments.steps, seed=arguments.seed, image=arguments.image,
        ),
        adapter=arguments.adapter, out=arguments.out, project=arguments.project, again=arguments.again,
        max_usd=arguments.max_usd, dry_run=arguments.dry_run,
    )
    price = "" if done.price is None else f", priced at ${done.price:.2f}"
    how = f"{done.model} through {done.backend}{price}"
    if done.state == generate.WOULD:
        print(f"would make {done.file}\n  with {how}")
    elif done.state == generate.HAD:
        print(f"had   {done.file}\n  made before with {how}; --again makes another")
    elif done.state == generate.WAITING:
        print(f"asked {how} for {done.file.name}\n  `media collect` brings it in when it is made")
    else:
        took = done.generation.params.get("took_s") if done.generation else None
        print(f"made  {done.file}\n  with {how}" + ("" if took is None else f", in {took:.0f} s"))


def run_generations(arguments: argparse.Namespace) -> None:
    from .. import generate

    if arguments.spend:
        spent = generate.spend()
        if not spent:
            print("nothing has been asked for yet")
            return
        wide = max(len(one.model) for one in spent)
        through = max(len(one.backend) for one in spent)
        for one in spent:
            print(f"  {one.model:<{wide}}  {one.backend:<{through}}  {one.status:<9}  {one.runs:>4} "
                  f"{'request' if one.runs == 1 else 'requests'}  ${one.usd:>8.2f}  {one.seconds:>7.1f} s of video")
        print(f"${sum(one.usd for one in spent):.2f} in all")
        return
    found = generate.listed(arguments.medium, arguments.status, limit=arguments.limit)
    if not found:
        print("nothing has been asked for yet" if arguments.medium is None and arguments.status is None
              else "nothing asked for matches")
        return
    for one in found:
        when = "" if one.created_at is None else f"{one.created_at:%Y-%m-%d %H:%M}  "
        print(f"  {when}{one.status:<9}  {one.medium:<5}  {one.model} through {one.backend}")
        print(f"      {_short(one.prompt)}")
        if one.status == "failed":
            print(f"      failed: {_short((one.error or 'no reason was given').splitlines()[-1], 100)}")
        elif one.file is not None:
            gone = "" if one.status != "succeeded" or one.file.is_file() else "  (the file is gone)"
            print(f"      {one.file}{gone}")
    print(f"{len(found)} {'request' if len(found) == 1 else 'requests'}")


def run_collect(arguments: argparse.Namespace) -> int:
    from .. import generate

    found = generate.collect()
    if not (found.made or found.failed or found.waiting or found.lost):
        print("no service has been handed anything that is still to come")
        return 0
    for one in found.made:
        print(f"  made     {one.file}")
    for one in found.failed:
        print(f"  failed   {_short(one.prompt, 48)}: {_short((one.error or 'no reason was given').splitlines()[-1], 100)}",
              file=sys.stderr)
    for one in found.waiting:
        print(f"  waiting  {_short(one.prompt, 48)}  ({one.backend})")
    for one in found.lost:
        print(f"  lost     {_short(one.prompt, 48)}: the {one.backend!r} adapter that was asked is not installed here",
              file=sys.stderr)
    return 1 if found.failed or found.lost else 0


def run_synthetic(arguments: argparse.Namespace) -> int:
    from .. import generate
    from ..edit import timelines

    if arguments.timeline is not None:
        timelines.load(arguments.timeline)
    found = generate.synthetic(arguments.timeline)
    where = arguments.timeline or "any cut"
    if not found:
        print(f"every clip of {where} was filmed" if arguments.timeline else "no cut holds a clip a model made")
        return 0
    wide = max(len(one.timeline) for one in found)
    for one in found:
        print(f"  {one.timeline:<{wide}}  clip {one.number:>3}  {one.track:<7}  {one.file}  "
              f"({one.model}: {_short(one.prompt, 48)})")
    clips = len({(one.timeline, one.number) for one in found})
    print(f"{clips} {'clip' if clips == 1 else 'clips'} that nobody filmed")
    return 1
