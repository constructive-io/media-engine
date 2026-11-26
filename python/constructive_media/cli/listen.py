"""Commands that listen: transcribe and vocabulary."""

from __future__ import annotations

import argparse
from typing import Any

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
