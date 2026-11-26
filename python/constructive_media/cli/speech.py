"""Commands for speech: who can speak, what speaks them, and a script said aloud."""

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

    command = commands.add_parser(
        "say", help="render a .conv script as sound",
        description="Say every line of a script in its persona's voice, listen back to each and say it again "
                    "until a take passes, put every sound in its place, and mix it all into one file. Beside "
                    "the output are kept every line as it was said, a manifest of how each was judged and "
                    "where everything landed, and each track on its own with --stems. The render is written "
                    "down, and so is every line that should be listened to.",
    )
    command.add_argument("script", help="a .conv script")
    command.add_argument("output", help="the file to write; its ending says what kind, such as .flac or .wav")
    command.add_argument("--banks", action="append", default=[], metavar="FOLDER",
                         help="where `@include NAME` finds NAME.sounds; give it again for more folders "
                              "(default: a `banks` folder beside the script, then one here)")
    command.add_argument("--sounds", action="append", default=[], metavar="FOLDER",
                         help="where sound files are looked for before the script's own folder; "
                              "give it again for more folders")
    command.add_argument("--stems", action="store_true", help="also write each track on its own")
    command.add_argument("--only", metavar="WHO", help="say again only these, personas or lines by number with "
                                                       "commas between, and keep every other line as it was")
    command.add_argument("--attempts", type=int, metavar="N", help="how many times a line is tried before more is done")
    command.add_argument("--reword", action="store_true",
                         help="when no take of a line passes, have the text model reword it and say that. "
                              "This changes what the script says")
    command.add_argument("--gap", type=float, metavar="SECONDS", help="the quiet after every line (default: 0.45)")
    command.add_argument("--loudness", type=float, metavar="LUFS", help="how loud the whole is (default: -16)")
    command.add_argument("--as-mixed", action="store_true", help="leave the loudness as the mix came out")
    command.add_argument("--without-generated", action="store_true",
                         help="leave out the sounds that are made from a prompt instead of stopping at them")
    command.add_argument("--label", metavar="NAME", help="the name the render is written down under "
                                                         "(default: the output's name)")
    command.set_defaults(run=run_say)


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


def run_say(arguments: argparse.Namespace) -> int | None:
    import os

    if not os.path.isfile(arguments.script):
        print(f"me: {arguments.script}: no such file", file=sys.stderr)
        return 2
    if arguments.as_mixed and arguments.loudness is not None:
        print("me: --loudness and --as-mixed cannot both be given", file=sys.stderr)
        return 2
    from .. import text
    from ..audio import line, scene

    how: dict[str, Any] = {}
    if arguments.attempts is not None:
        how["attempts"] = arguments.attempts
    if arguments.reword:
        how["reword"] = line.rewording(text.writer())
    report = scene.render(
        arguments.script, arguments.output, banks=arguments.banks, sounds=arguments.sounds, stems=arguments.stems,
        only=arguments.only, gap=scene.GAP if arguments.gap is None else arguments.gap,
        loudness=None if arguments.as_mixed else scene.LOUDNESS if arguments.loudness is None else arguments.loudness,
        label=arguments.label, without_generated=arguments.without_generated, tell=print, **how,
    )
    scene.record(report)
    said = report["lines"]
    print(f"{len(said)} lines, {report['seconds']:.1f} seconds: {arguments.output}")
    if report["sound"]["left_out"]:
        print(f"left out, having no file: {', '.join(report['sound']['left_out'])}")
    listen = [str(one["line"]) for one in said if one["status"] == line.NEEDS_REVIEW]
    if listen:
        print(f"{len(listen)} need a listen: {', '.join(listen)}")
    return None
