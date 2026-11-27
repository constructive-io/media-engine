"""Commands for worn microphones: who wore which, and so who spoke each line."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def register(commands: Any) -> None:
    from ..takes.microphones import FLOOR_DB, MARGIN_DB

    command = commands.add_parser(
        "wear", help="say who wore a microphone in a take",
        description="Tie one recording of a take to the person who wore it. `media microphones` then "
                    "gives that person the lines their microphone heard loudest.",
    )
    command.add_argument("take", help="the take's name")
    command.add_argument("recording", help="a regular expression matching one of its recordings, by label or path")
    command.add_argument("who", nargs="?", help="the person's name, or their slug")
    command.add_argument("--new", action="store_true", help="add this person if they are not known yet")
    command.add_argument("--character", action="store_true",
                         help="with --new: an invented character, not a real person")
    command.add_argument("--clear", action="store_true", help="say that nobody wore it")
    command.set_defaults(run=run_wear)

    command = commands.add_parser(
        "microphones", help="tell who spoke each line of a take by whose microphone heard it loudest",
        description="For each transcribed line of a take, compare its worn microphones over the moment "
                    "it was said, each against its own loud level, and give the line to the wearer of the "
                    "one that leads. A line no microphone leads clearly is left without a name, and a line "
                    "a person named is never changed. Each microphone is then checked against the voices "
                    "`media diarize` found: it should carry one.",
    )
    command.add_argument("take", help="the take's name")
    command.add_argument("--margin", type=float, default=MARGIN_DB, metavar="DB",
                         help="how far the loudest microphone must lead the next, in decibels (default %(default)g)")
    command.add_argument("--floor", type=float, default=FLOOR_DB, metavar="DB",
                         help="how far under its own loud level a microphone may be and still win a line "
                              "(default %(default)g)")
    command.add_argument("--clips", action="store_true",
                         help="cut a few seconds of what each microphone won, to listen to")
    command.add_argument("--dry-run", action="store_true", help="show what would be written and write nothing")
    command.set_defaults(run=run_microphones)


def run_wear(arguments: argparse.Namespace) -> int | None:
    from ..takes import microphones

    if (arguments.who is None) != arguments.clear:
        print("media: give a name, or --clear to say nobody wore it", file=sys.stderr)
        return 2
    recording, person = microphones.wear(
        arguments.take, arguments.recording, arguments.who, new=arguments.new,
        kind="character" if arguments.character else "person",
    )
    print(f"{recording} in {arguments.take} was worn by {person or 'nobody'}")
    return None


def run_microphones(arguments: argparse.Namespace) -> None:
    from .. import takes
    from ..takes import microphones

    reading = microphones.read(takes.find(arguments.take), margin_db=arguments.margin, floor_db=arguments.floor)
    clips = microphones.clips(reading) if arguments.clips else {}
    print(f"{reading.take.name}: {len(reading.microphones)} microphones, {len(reading.lines)} lines")
    won = microphones.won(reading)
    wide = max(len(one.microphone.name) for one in won)
    named = max(len(one.microphone.wearer or "nobody yet") for one in won)
    for one in won:
        minutes, seconds = divmod(round(one.seconds), 60)
        lead = "" if one.lead_db is None else f"  leads by {one.lead_db:.0f} dB"
        print(f"  {one.microphone.name:<{wide}}  {one.microphone.wearer or 'nobody yet':<{named}}  "
              f"{one.lines:>5} lines  {minutes:>3}:{seconds:02d}{lead}{_voice(one)}")
        if one.microphone.asset_id in clips:
            print(f"  {'':<{wide}}  listen: {clips[one.microphone.asset_id]}")
    for name in reading.silent:
        print(f"  {name} recorded nothing")
    for outcome, what in (
        (microphones.UNCLEAR, f"unclear: no microphone led the next by {reading.margin_db:g} dB"),
        (microphones.UNHEARD, "said where fewer than two microphones were recording"),
        (microphones.UNWORN, "won by a microphone nobody is said to wear; `media wear` says who"),
        (microphones.BY_HAND, "already named by a person, and left as they are"),
        (microphones.DISAGREES, "left alone: a person named that voice as someone else"),
    ):
        if reading.count(outcome):
            print(f"  {reading.count(outcome)} lines {what}")
    if not any(line.voice for line in reading.lines):
        print("  no voices have been found in these recordings; after `media diarize` each microphone "
              "is checked against them")
    given = reading.count(microphones.GIVEN) if arguments.dry_run else microphones.write(reading)
    print(f"{given} of {len(reading.lines)} lines given a speaker"
          + ("; nothing was written" if arguments.dry_run else ""))


def _voice(one: Any) -> str:
    """Whether one voice says what a microphone won."""
    voice = one.voice
    if voice is None:
        return ""
    whose = f"{voice.label} of {voice.recording}"
    if not voice.alone:
        return f"  {voice.share:.0%} is {whose}: it may be carrying more than one person"
    other = voice.owner and voice.owner != one.microphone.wearer
    return f"  one voice: {whose}" + (f", named {voice.owner}" if other else "")
