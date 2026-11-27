"""Commands that listen: what is said, and who says it."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..speakers.cluster import ALIKE
from .analyze import MATCH_HELP


def register(commands: Any) -> None:
    command = commands.add_parser("transcribe", help="write down what is said in each recording, word by word")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--language", default="en",
                         help="the language spoken, as a two-letter code (default %(default)s)")
    command.add_argument("--force", action="store_true",
                         help="transcribe again, replacing the transcript a recording already has")
    command.add_argument("--no-vocabulary", action="store_true",
                         help="tell the recogniser nothing about the project's vocabulary")
    command.add_argument("--adapter", help="the transcription adapter to use")
    command.set_defaults(run=run_transcribe)

    command = commands.add_parser(
        "vocabulary", help="list the words to listen for, or add some",
        description="With terms, add them to the vocabulary. With none, list what a recording is "
                    "transcribed with: the terms of no project, and those of --project if you name one.",
    )
    command.add_argument("terms", nargs="*", metavar="term", help="a name or term to listen for")
    command.add_argument("--project", help="listen for it only in this project's recordings")
    command.add_argument("--kind", help="what it is, for your own reference: person, company, technical")
    command.set_defaults(run=run_vocabulary)

    command = commands.add_parser(
        "diarize", help="find the voices in each transcribed recording, and who speaks each line",
        description="Group a recording's speech by voice. Each voice gets a label (voice1 is whoever "
                    "speaks most) and a card: a few seconds of it to listen to. Name them with `media name`.",
    )
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--voices", type=int, metavar="N",
                         help="how many people speak, when you know; otherwise it is worked out")
    command.add_argument("--alike", type=float, metavar="0-1", default=ALIKE,
                         help="how alike two groups of speech must sound to be one person "
                              "(default %(default)s; higher finds more voices)")
    command.add_argument("--force", action="store_true",
                         help="go over recordings again; voices that were named keep their names")
    command.add_argument("--no-cards", action="store_true", help="do not cut a card for each voice")
    command.add_argument("--adapter", help="the speaker adapter to use")
    command.set_defaults(run=run_diarize)

    command = commands.add_parser("speakers", help="list the voices found in each recording, and whose they are")
    command.add_argument("match", nargs="?", help=MATCH_HELP.replace("assets", "recordings"))
    command.set_defaults(run=run_speakers)

    command = commands.add_parser(
        "name", help="say whose voice it is",
        description="Tie one voice of one recording to a person. Every line it speaks there is then theirs.",
    )
    command.add_argument("recording", help="a regular expression matching one recording's path")
    command.add_argument("voice", help="the voice's label, as `media speakers` shows it: voice1, voice2")
    command.add_argument("who", nargs="?", help="the person's name, or their slug")
    command.add_argument("--new", action="store_true", help="add this person if they are not known yet")
    command.add_argument("--character", action="store_true",
                         help="with --new: an invented character, not a real person")
    command.add_argument("--clear", action="store_true", help="take the name off the voice")
    command.set_defaults(run=run_name)


def run_transcribe(arguments: argparse.Namespace) -> None:
    from ..audio import transcripts
    from ..transcription import transcriber

    listener = transcriber(arguments.adapter)
    done = transcripts.transcribe(
        arguments.match, language=arguments.language, force=arguments.force,
        vocabulary=not arguments.no_vocabulary, transcriber=listener,
    )
    for name, count in done:
        print(f"  {count:>5} segments  {name}")
    print(f"{len(done)} recordings transcribed with {listener.name} ({listener.model})")


def run_vocabulary(arguments: argparse.Namespace) -> None:
    from ..audio import transcripts

    if arguments.terms:
        added = transcripts.learn(arguments.terms, arguments.project, arguments.kind)
        print(f"{added} of {len(arguments.terms)} terms added")
        return
    listed = transcripts.terms(arguments.project)
    for term in listed:
        print(f"  {term}")
    print(f"{len(listed)} terms")


def run_diarize(arguments: argparse.Namespace) -> None:
    from ..speakers import cards, embedder, voices

    listener = embedder(arguments.adapter)
    done = voices.find(
        arguments.match, voices=arguments.voices, alike=arguments.alike, force=arguments.force,
        embedder=listener,
    )
    for found in done:
        if not arguments.no_cards:
            cards.make(found.asset_id, found.source, found.telling.voices)
        print(f"  {found.name}")
        for voice in found.telling.voices:
            _voice(voice.label, voice.segments, voice.speech_s, voice.cohesion, voice.persona, voice.like,
                   cards.card(found.asset_id, voice.label))
        if found.telling.unknown:
            print(f"    {found.telling.unknown} segments left unknown: too short or too unclear to tell")
    print(f"{len(done)} recordings gone over with {listener.name} ({listener.model}): "
          f"{sum(len(found.telling.voices) for found in done)} voices")


def run_speakers(arguments: argparse.Namespace) -> None:
    from ..speakers import cards, names

    recordings = names.listed(arguments.match)
    for recording in recordings:
        print(f"  {recording.name}")
        for voice in recording.voices:
            _voice(voice.label, voice.segments, voice.speech_s, voice.cohesion, voice.persona, voice.like,
                   cards.card(recording.asset_id, voice.label))
        if recording.unknown:
            print(f"    {recording.unknown} segments unknown")
    unnamed = sum(voice.persona is None for recording in recordings for voice in recording.voices)
    print(f"{len(recordings)} recordings, {unnamed} voices without a name")


def run_name(arguments: argparse.Namespace) -> int | None:
    from ..speakers import names

    if (arguments.who is None) != arguments.clear:
        print("media: give a name, or --clear to take one off", file=sys.stderr)
        return 2
    recording, segments = names.name(
        arguments.recording, arguments.voice, arguments.who, new=arguments.new,
        kind="character" if arguments.character else "person",
    )
    whose = f"is {arguments.who}" if arguments.who else "has no name"
    print(f"{arguments.voice} in {recording} {whose}: {segments} segments")
    return None


#: Under this a voice's speech is not much alike: likely more than one person.
MIXED = 0.5


def _voice(label: str, segments: int, speech_s: float, cohesion: float | None,
           persona: str | None, like: str | None, card: Any) -> None:
    whose = persona or (f"sounds like {like}" if like else "")
    minutes, seconds = divmod(round(speech_s), 60)
    mixed = "  may be more than one person" if cohesion is not None and cohesion < MIXED else ""
    print(f"    {label:<8} {segments:>5} segments  {minutes:>3}:{seconds:02d}  {whose}{mixed}".rstrip())
    if card is not None and not persona:
        print(f"             listen: {card}")
