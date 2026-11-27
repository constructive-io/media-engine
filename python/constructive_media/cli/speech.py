"""Commands for speech: who can speak, what speaks them, and a script said aloud."""

from __future__ import annotations

import argparse
import os
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
    command.add_argument("--said", metavar="TEXT",
                         help="the words said in that recording (default: what is in the .txt beside it)")
    command.add_argument("--provisional", action="store_true", help="mark it as not settled yet")
    command.add_argument("--new", action="store_true", help="add the persona if they are not there yet")
    command.add_argument("--drop", action="store_true",
                         help="take the voice away: the one for --emotion, or all of the persona's")
    command.set_defaults(run=run_voice)

    command = commands.add_parser(
        "say", help="render a .conv script as sound, or say a few words in one voice",
        description="Say every line of a script in its persona's voice, listen back to each and say it again "
                    "until a take passes, put every sound in its place, and mix it all into one file. Beside "
                    "the output are kept every line as it was said, a manifest of how each was judged and "
                    "where everything landed, and each track on its own with --stems. The render is written "
                    "down, and so is every line that should be listened to. With --voice there is no script: "
                    "the words are given, said in that persona's voice, checked the same way and written as "
                    "the output, as in `me say --voice ada \"The lantern is lit.\" out.wav`.",
    )
    command.add_argument("script", help="a .conv script; with --voice, the words to say")
    command.add_argument("output", help="the file to write; its ending says what kind, such as .flac or .wav")
    command.add_argument("--voice", metavar="WHO", help="say the words given, in this persona's voice, "
                                                        "instead of rendering a script")
    command.add_argument("--emotion", metavar="NAME", help="with --voice: the emotion to say them in "
                                                           "(default: neutral)")
    command.add_argument("--seed", type=int, metavar="N", help="with --voice: the seed of the first take")
    command.add_argument("--banks", action="append", default=[], metavar="FOLDER",
                         help="where `@include NAME` finds NAME.sounds; give it again for more folders "
                              "(default: a `banks` folder beside the script, then one here; the banks of "
                              "libraries added with `me library add` are looked in after either)")
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


    command = commands.add_parser(
        "reference", help="measure a recording of a voice, or cut it into a clip a voice can be cloned from",
        description="With only a recording: say how long it is, how loud, and what would make it a poor clip to "
                    "clone a voice from. With an output as well: cut the recording into such a clip, with the "
                    "rumble and the quiet at its ends taken off, as one channel at 24 kHz and -19 LUFS, and "
                    "write its words beside it in a .txt file. The words are the ones given with --said, or "
                    "with --listen the ones a transcriber hears in the clip as cut, for you to read against "
                    "the sound and put right. A clip is best between 15 and 28 seconds, of one person "
                    "speaking one way, ending on a calm phrase.",
    )
    command.add_argument("recording", help="a recording of the voice")
    command.add_argument("output", nargs="?", help="the clip to write, such as voices/ada/neutral.flac")
    command.add_argument("--from", dest="start", type=float, metavar="SECONDS", help="where the clip starts")
    command.add_argument("--to", dest="end", type=float, metavar="SECONDS", help="where the clip ends")
    command.add_argument("--said", metavar="TEXT", help="the words said in the clip")
    command.add_argument("--listen", action="store_true",
                         help="have the transcriber write down the words, when --said does not give them")
    command.add_argument("--json", action="store_true", help="print what was measured as JSON")
    command.set_defaults(run=run_reference)

    command = commands.add_parser(
        "audition", help="have a voice say lines it has never seen, and count what passed",
        description="Say a set of lines in a persona's voice, once each with no second try, listen back to "
                    "every one, and say how many came out as written and whether any began with the last "
                    "words of the recording the voice was made from. The takes are left in a folder to be "
                    "heard. A voice that fails here will cost takes on every script it speaks in.",
    )
    command.add_argument("who", help="the persona's name, or their slug")
    command.add_argument("--emotion", metavar="NAME", help="the emotion to audition (default: neutral)")
    command.add_argument("--lines", metavar="FILE", help="a text file of lines to say, one to a line "
                                                         "(default: eight of the engine's own)")
    command.add_argument("--out", metavar="FOLDER", help="where the takes are left (default: audition/WHO-EMOTION)")
    command.add_argument("--seed", type=int, metavar="N", help="the seed the takes start from")
    command.add_argument("--json", action="store_true", help="print the whole report as JSON")
    command.set_defaults(run=run_audition)


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
    reference = arguments.reference
    if reference and os.path.isfile(reference):
        reference = os.path.abspath(reference)   # found again from wherever the next command is run
    said = arguments.said
    beside = os.path.splitext(reference)[0] + ".txt" if reference else None
    if said is None and beside and os.path.isfile(beside):   # where `me reference` writes a clip's words
        with open(beside, encoding="utf-8") as written:
            said = written.read().strip() or None
    voice = speech.Voice(arguments.who, emotion, arguments.engine, arguments.profile, reference, said,
                         arguments.seed)
    replaced = speech.keep(voice, new=arguments.new, status="provisional" if arguments.provisional else "ready")
    print(f"{arguments.who} {'has a new' if replaced else 'now has a'} {emotion} voice: {arguments.profile}")
    return None


_OF_A_SCRIPT = ("banks", "sounds", "stems", "only", "reword", "gap", "loudness", "as_mixed", "without_generated",
                "label")


def run_say(arguments: argparse.Namespace) -> int | None:
    if arguments.voice:
        return _say_words(arguments)
    if arguments.emotion or arguments.seed is not None:
        print("me: --emotion and --seed go with --voice; in a script they are written on the line",
              file=sys.stderr)
        return 2
    if not os.path.isfile(arguments.script):
        print(f"me: {arguments.script}: no such file", file=sys.stderr)
        return 2
    if arguments.as_mixed and arguments.loudness is not None:
        print("me: --loudness and --as-mixed cannot both be given", file=sys.stderr)
        return 2
    from .. import conv, libraries, text
    from ..audio import line, scene

    how: dict[str, Any] = {}
    if arguments.attempts is not None:
        how["attempts"] = arguments.attempts
    if arguments.reword:
        how["reword"] = line.rewording(text.writer())
    report = scene.render(
        arguments.script, arguments.output, banks=[*conv.bank_folders(arguments.script, arguments.banks), *libraries.banks()],
        sounds=arguments.sounds, shared=libraries.sounds(), stems=arguments.stems,
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


def _say_words(arguments: argparse.Namespace) -> int | None:
    given = [name for name in _OF_A_SCRIPT if getattr(arguments, name) not in (None, False, [])]
    if given:
        print(f"me: --{given[0].replace('_', '-')} is for a script; --voice says the words it is given",
              file=sys.stderr)
        return 2
    from ..audio import line, voice
    from ..speakers.names import slug

    how = {"attempts": arguments.attempts} if arguments.attempts is not None else {}
    told = voice.say(arguments.script, arguments.output, persona=slug(arguments.voice), emotion=arguments.emotion,
                     seed=arguments.seed, **how)
    takes = "1 take" if told["attempts"] == 1 else f"{told['attempts']} takes"
    print(f"{told['persona']}/{told['emotion']} seed {told['seed']}, {takes}, {told['status']}: {arguments.output}")
    if told["status"] == line.NEEDS_REVIEW:
        print(f"no take passed; this is the best of them. It was heard as: {told['heard']}")
        return 1
    return None


def run_reference(arguments: argparse.Namespace) -> int | None:
    import json

    from ..audio import voice

    if not arguments.output:
        if arguments.start is not None or arguments.end is not None or arguments.said or arguments.listen:
            print("me: give the clip to write as well; with only a recording it is measured and nothing is cut",
                  file=sys.stderr)
            return 2
        measured = voice.measure(arguments.recording)
        stops, notes = voice.fit(measured)
        if arguments.json:
            print(json.dumps({**measured, "stops": stops, "notes": notes}, indent=2))
        else:
            print(_measured(measured))
            for said in (*stops, *notes):
                print(f"  {said}")
            if not stops and not notes:
                print("  fit to clone a voice from, by its length and shape. Whether it is one person, speaking "
                      "one way, in a quiet room, is for you to hear")
        return 1 if stops else None
    listens = None
    if arguments.listen and not arguments.said:
        from ..transcription import transcriber

        listens = transcriber()
    told = voice.reference(arguments.recording, arguments.output, start=arguments.start, end=arguments.end,
                           said=arguments.said, transcriber=listens)
    if arguments.json:
        print(json.dumps(told, indent=2, ensure_ascii=False))
        return None
    print(_measured(told["is"]))
    for said in told["notes"]:
        print(f"  {said}")
    if told["said_file"]:
        print(f"its words{', as they were heard,' if told['heard'] else ''} are in {told['said_file']}"
              + (": read them against the clip and put right what was misheard" if told["heard"] else ""))
    else:
        print("its words are not written down yet: give them with --said, or have them heard with --listen")
    return None


def _measured(measured: dict[str, Any]) -> str:
    loud = "silent" if measured["loudness"] is None else f"{measured['loudness']:g} LUFS, peak {measured['peak']:g} dB"
    channels = "1 channel" if measured["channels"] == 1 else f"{measured['channels']} channels"
    return f"{measured['file']}: {measured['seconds']:.1f} seconds, {channels} at {measured['rate']} Hz, {loud}"


def run_audition(arguments: argparse.Namespace) -> int | None:
    import json

    from .. import speech
    from ..audio import voice
    from ..speakers.names import slug

    lines = voice.LINES
    if arguments.lines:
        if not os.path.isfile(arguments.lines):
            print(f"me: {arguments.lines}: no such file", file=sys.stderr)
            return 2
        with open(arguments.lines, encoding="utf-8") as file:
            lines = [line.strip() for line in file if line.strip() and not line.lstrip().startswith("#")]
    who = slug(arguments.who)
    folder = arguments.out or os.path.join("audition", f"{who}-{arguments.emotion or speech.NEUTRAL}")
    told = voice.audition(who, folder, emotion=arguments.emotion, lines=lines, seed=arguments.seed,
                          tell=None if arguments.json else print)
    if arguments.json:
        print(json.dumps(told, indent=2, ensure_ascii=False))
    else:
        print(f"{told['persona']}/{told['emotion']}: {told['good']} of {told['of']} lines came out as written; "
              f"the takes are in {told['folder']}")
        if told["bled"]:
            print(f"{len(told['bled'])} began with the end of the voice's recording: cut the clip so that it "
                  "ends on a calm phrase with quiet after it, and write its words down exactly")
    return None if told["passed"] else 1
