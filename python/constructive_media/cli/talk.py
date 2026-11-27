"""The command for talking with a text model aloud."""

from __future__ import annotations

import argparse
import sys
from typing import Any

#: Where the page is served unless another port is asked for.
PORT = 8850


def register(commands: Any) -> None:
    command = commands.add_parser(
        "talk", help="talk with a text model aloud, in your browser",
        description="Serve a page you can talk to. What you say is written down by the transcription model, "
                    "answered by the text model and said back by the speech model, a sentence at a time, so the "
                    "first sentence is heard while the rest is still being written. Speak or type while it is "
                    "answering and it stops. It serves this machine only, until it is stopped. With --measure "
                    "nothing is served: each of the three models is timed, and the wait they add up to.",
    )
    command.add_argument("--voice", metavar="WHO", help="answer in this persona's voice, as the database has it")
    command.add_argument("--emotion", metavar="NAME", help="with --voice: the emotion to answer in (default: neutral)")
    command.add_argument("--profile", metavar="NAME",
                         help="answer in a voice the speech adapter knows by this name, with no database asked")
    command.add_argument("--quick", metavar="NAME",
                         help="with --voice: say each sentence in this voice of the speech adapter's own, which "
                              "is quick, and have the voice_conversion adapter say it again in the persona's")
    command.add_argument("--system", metavar="TEXT", help="what the text model is told it is doing, in place of "
                                                          "being told it is talking aloud")
    command.add_argument("--language", default="en", metavar="CODE", help="the language spoken to it (default: en)")
    command.add_argument("--typed", action="store_true", help="listen to nothing: turns are typed, and still answered aloud")
    command.add_argument("--port", type=int, default=PORT, metavar="N",
                         help=f"the port to serve on (default: {PORT}; 0 for any free one)")
    command.add_argument("--measure", nargs="?", const="", metavar="RECORDING",
                         help="serve nothing: time each model, the first time and once warm. With a recording of "
                              "something said, that is what is written down; without one, a line the voice says")
    command.add_argument("--json", action="store_true", help="with --measure: print what was measured as JSON")
    command.set_defaults(run=run_talk)


def run_talk(arguments: argparse.Namespace) -> int | None:
    if arguments.voice and arguments.profile:
        print("media: --voice is a persona the database has and --profile a name the speech adapter has; "
              "give one of them", file=sys.stderr)
        return 2
    if arguments.emotion and not arguments.voice:
        print("media: --emotion goes with --voice", file=sys.stderr)
        return 2
    if arguments.quick and not arguments.voice:
        print("media: --quick goes with --voice: the voice it is said again in", file=sys.stderr)
        return 2
    if arguments.json and arguments.measure is None:
        print("media: --json goes with --measure", file=sys.stderr)
        return 2
    from .. import speech, talk, text, transcription
    from ..settings import Settings

    settings = Settings.from_env()
    if arguments.voice:
        from ..speakers.names import slug

        voice = speech.StoredVoices().find(slug(arguments.voice), arguments.emotion)
    else:
        voice = speech.Voice("voice", profile=arguments.profile)
    if arguments.quick:
        from .. import conversion

        # Found out now, and not at the first sentence: a voice with no clip cannot be said again in.
        conversion.clip(voice)
        speaks: Any = conversion.Respoken(speech.synthesizer(settings=settings),
                                          speech.Voice("quick", profile=arguments.quick),
                                          conversion.converter(settings=settings))
    else:
        speaks = speech.synthesizer(voice.engine, settings=settings)
    parts = talk.Parts(
        writer=text.writer(settings=settings),
        synthesizer=speaks,
        voice=voice,
        transcriber=None if arguments.typed else transcription.transcriber(settings=settings),
        system=arguments.system or talk.SYSTEM,
        language=arguments.language,
    )
    if arguments.measure is not None:
        return _measure(parts, arguments)
    return _serve(parts, settings, arguments)


def _named(model: Any) -> str:
    return " ".join(str(part) for part in (getattr(model, "name", ""), getattr(model, "model", "")) if part)


def _serve(parts: Any, settings: Any, arguments: argparse.Namespace) -> int | None:
    import tempfile
    import time
    from pathlib import Path

    from ..talk import hearing, serve

    # A voice that cannot speak, or a model that cannot listen, is found out here and not at the first turn.
    # And the first time either is used is the slow one, so it is not somebody's first sentence.
    with tempfile.TemporaryDirectory(prefix="talk-") as folder:
        began = time.perf_counter()
        said = parts.synthesizer.say("Ready.", Path(folder) / "ready.wav", voice=parts.voice, seed=parts.voice.seed)
        print(f"the voice is ready, in {time.perf_counter() - began:.1f} s", flush=True)
        if parts.transcriber is not None:
            began = time.perf_counter()
            parts.transcriber.transcribe(said, language=parts.language)
            print(f"the listening is ready, in {time.perf_counter() - began:.1f} s", flush=True)
    ears = hearing.ears(settings, tell=lambda told: print(told, file=sys.stderr)) if parts.transcriber else None
    about = {"text": _named(parts.writer), "speech": _named(parts.synthesizer)}
    if parts.transcriber is not None:
        about["transcription"] = _named(parts.transcriber)
    with serve.Talk(parts, ears=ears, port=arguments.port, about=about) as served:
        print(served.address, flush=True)
        heard = f"speech is told from the room by {ears().name}" if ears else "turns are typed"
        print(f"{heard}; stop with Ctrl-C", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print()
    return None


def _measure(parts: Any, arguments: argparse.Namespace) -> int | None:
    import os

    from ..talk import measure

    if arguments.measure and not os.path.isfile(arguments.measure):
        print(f"media: {arguments.measure}: no such file", file=sys.stderr)
        return 2
    told = measure.measure(parts, arguments.measure or None)
    failed = any("trouble" in part for part in told["parts"])
    if arguments.json:
        import json

        print(json.dumps(told, indent=2))
        return 1 if failed else None
    # Two models end to end have a long name: the columns are as wide as the longest of them.
    adapter = max(12, *(len(part["adapter"]) for part in told["parts"]))
    model = max(34, *(len(part["model"]) for part in told["parts"]))
    print(f"  {'':<10} {'adapter':<{adapter}} {'model':<{model}} {'first time':>10} {'warm':>9}")
    for part in told["parts"]:
        head = f"  {part['part']:<10} {part['adapter']:<{adapter}} {part['model']:<{model}}"
        if "trouble" in part:
            print(f"{head} failed: {part['trouble']}")
            continue
        print(f"{head} {part['first_ms']:>7} ms {part['ms']:>6} ms")
        if part.get("first_word_ms") is not None:
            print(f"  {'':<10} its first word came in {part['first_word_ms']} ms")
        for said in ("heard", "said"):
            if part.get(said):
                print(f"  {'':<10} {said}: {part[said]}")
    if told["wait_ms"] is None:
        print("no wait can be given until every part works")
        return 1
    print(f"from the end of your sentence to the first sound: {told['wait_ms']} ms, once warm")
    return None
