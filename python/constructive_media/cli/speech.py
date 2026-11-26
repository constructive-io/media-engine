"""Commands for voices: who can speak, and what speaks them."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def register(commands: Any) -> None:
    command = commands.add_parser(
        "voices", help="list the voices that can speak",
        description="List every voice: whose it is, the emotion it speaks in, the adapter that speaks it "
                    "and what that adapter knows it by.",
    )
    command.set_defaults(run=run_voices)

    command = commands.add_parser(
        "voice", help="give somebody a voice, or take one away",
        description="Write down a voice for a persona: the adapter that speaks it and what that adapter "
                    "knows it by. A persona has one voice for each emotion; a second for the same emotion "
                    "takes the place of the first. No sound is kept: a voice is a name, and where its "
                    "reference recording is when it has one.",
    )
    command.add_argument("who", help="the persona's name, or their slug")
    command.add_argument("profile", nargs="?", help="what the adapter knows the voice by")
    command.add_argument("--emotion", metavar="NAME", help="the emotion this voice speaks in (default: neutral)")
    command.add_argument("--engine", metavar="NAME", help="the speech adapter that speaks it (default: the configured one)")
    command.add_argument("--seed", type=int, metavar="N", help="always say this voice's lines with this seed")
    command.add_argument("--reference", metavar="FILE", help="the recording the voice was made from")
    command.add_argument("--said", metavar="TEXT", help="the words said in that recording")
    command.add_argument("--provisional", action="store_true", help="mark it as not settled yet")
    command.add_argument("--new", action="store_true", help="add the persona if they are not there yet")
    command.add_argument("--drop", action="store_true",
                         help="take the voice away: the one for --emotion, or all of the persona's")
    command.set_defaults(run=run_voice)


def run_voices(arguments: argparse.Namespace) -> None:
    from .. import speech

    kept = speech.kept()
    for one in kept:
        voice = one.voice
        notes = [f"seed {voice.seed}"] if voice.seed is not None else []
        notes += ["from a recording"] if voice.reference_audio else []
        notes += [one.status] if one.status != "ready" else []
        print(f"  {voice.persona:<20} {voice.emotion:<10} {voice.engine:<12} {voice.profile}"
              + (f"  ({', '.join(notes)})" if notes else ""))
    people = len({one.voice.persona for one in kept})
    print(f"{len(kept)} voices for {people} personas")


def run_voice(arguments: argparse.Namespace) -> int | None:
    from .. import adapters, speech
    from ..speakers.names import slug

    if arguments.drop:
        if arguments.profile:
            print("me: --drop takes a voice away; it does not take a profile", file=sys.stderr)
            return 2
        gone = speech.drop(slug(arguments.who), arguments.emotion)
        print(f"{gone} voices of {arguments.who} taken away")
        return None if gone else 1
    if not arguments.profile:
        print("me: give the profile, what the adapter knows the voice by, or --drop to take a voice away",
              file=sys.stderr)
        return 2
    if arguments.engine:
        adapters.registry().describe("speech", arguments.engine)  # a name nothing answers to is said now
    emotion = arguments.emotion or speech.NEUTRAL
    voice = speech.Voice(arguments.who, emotion, arguments.engine, arguments.profile, arguments.reference,
                         arguments.said, arguments.seed)
    replaced = speech.keep(voice, new=arguments.new, status="provisional" if arguments.provisional else "ready")
    print(f"{arguments.who} {'has a new' if replaced else 'now has a'} {emotion} voice: {arguments.profile}")
    return None
