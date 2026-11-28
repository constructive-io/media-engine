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
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--language", default="en",
                         help="the language spoken, as a two-letter code (default %(default)s)")
    command.add_argument("--force", action="store_true",
                         help="transcribe again, replacing the transcript a recording already has")
    command.add_argument("--no-vocabulary", action="store_true",
                         help="tell the recogniser nothing about the project's vocabulary")
    command.add_argument("--vocabulary-of", metavar="PROJECT",
                         help="listen for this project's terms, in place of those of the project each "
                              "recording belongs to")
    command.add_argument("--from", dest="source", metavar="FILE",
                         help="listen to this file in place of the one recording that matches: the same "
                              "sound cleaned up, with the same times")
    command.add_argument("--speaker", metavar="NAME",
                         help="write this beside every line, for a microphone only one person wore")
    command.add_argument("--adapter", help="the transcription adapter to use")
    command.set_defaults(run=run_transcribe)

    command = commands.add_parser(
        "vocabulary", help="list the words to listen for, add some, or say what one is misheard as",
        description="With terms, add them to the vocabulary. With none, list what a recording is "
                    "transcribed with: the terms of no project, and those of --project if you name one. "
                    "With one term and --heard-as, record what the recogniser writes in its place, "
                    "for `media mend` to put right.",
    )
    command.add_argument("terms", nargs="*", metavar="term", help="a name or term to listen for")
    command.add_argument("--project", help="listen for it only in this project's recordings")
    command.add_argument("--kind", help="what it is, for your own reference: person, company, technical")
    command.add_argument("--heard-as", action="append", metavar="WORDS", default=[],
                         help="what the recogniser writes when this one term is said; give it again for each")
    command.set_defaults(run=run_vocabulary)

    command = commands.add_parser(
        "mend", help="put right what is known to be misheard, in every transcript",
        description="Rewrite each mishearing recorded with `media vocabulary TERM --heard-as` as its term, "
                    "in the lines and in their words, keeping a plural or possessive. What the recogniser "
                    "wrote is kept beside it and each change is recorded, so --undo puts all of it back. "
                    "Run twice, the second finds nothing to do.",
    )
    command.add_argument("--match", help=MATCH_HELP.replace("assets", "recordings"))
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--dry-run", action="store_true", help="show what would change and change nothing")
    command.add_argument("--undo", action="store_true",
                         help="put every line and word back to what the recogniser wrote")
    command.set_defaults(run=run_mend)

    command = commands.add_parser(
        "relisten", help="transcribe the weakest recordings again, and keep what is better",
        description="Find the recordings the recogniser was least sure of, or wrote little for, and "
                    "listen to them again with another: --adapter, or a larger model in the settings. "
                    "A new transcript replaces the old only where it is surer and found no less. "
                    "Where it does, find the recording's voices and passages again.",
    )
    command.add_argument("--match", help=MATCH_HELP.replace("assets", "recordings"))
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--language", default="en",
                         help="the language spoken, as a two-letter code (default %(default)s)")
    command.add_argument("--below", type=float, metavar="SURENESS", default=None,
                         help="listen again where the recogniser was less sure than this on average, as "
                              "the log-probability of its words (default -0.55; 0 is certain)")
    command.add_argument("--sparse", type=float, metavar="A_MINUTE", default=None,
                         help="or where it wrote fewer segments a minute than this (default 2)")
    command.add_argument("--min-length", type=float, metavar="SECONDS", default=None,
                         help="leave alone recordings shorter than this (default 60)")
    command.add_argument("--limit", type=int, metavar="N", help="listen to no more than this many, weakest first")
    command.add_argument("--no-vocabulary", action="store_true",
                         help="tell the recogniser nothing about the project's vocabulary")
    command.add_argument("--dry-run", action="store_true",
                         help="list the recordings that would be listened to, and listen to none")
    command.add_argument("--adapter", help="the transcription adapter to use")
    command.set_defaults(run=run_relisten)

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
    command.add_argument("--dry-run", action="store_true",
                         help="show the voices that would be found; store nothing and cut no cards")
    command.add_argument("--adapter", help="the speaker adapter to use")
    command.set_defaults(run=run_diarize)

    command = commands.add_parser("speakers", help="list the voices found in each recording, and whose they are")
    command.add_argument("match", nargs="?", help=MATCH_HELP.replace("assets", "recordings"))
    command.add_argument("--people", action="store_true",
                         help="add each person up over every recording: how many they speak in, how many "
                              "lines, and for how long")
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
        arguments.match, project=arguments.project, language=arguments.language, force=arguments.force,
        vocabulary=not arguments.no_vocabulary, source=arguments.source, speaker=arguments.speaker,
        transcriber=listener, vocabulary_of=arguments.vocabulary_of,
    )
    for name, count in done:
        print(f"  {count:>5} segments  {name}")
    print(f"{len(done)} recordings transcribed with {listener.name} ({listener.model})")


def run_vocabulary(arguments: argparse.Namespace) -> int | None:
    from ..audio import transcripts, upkeep

    if arguments.heard_as:
        if len(arguments.terms) != 1:
            print("media: --heard-as is what one term is misheard as: give the one term", file=sys.stderr)
            return 2
        (term,) = arguments.terms
        added = upkeep.mishear(term, arguments.heard_as, arguments.project, arguments.kind)
        print(f"{term} is heard as {added} more: `media mend` puts them right")
        return None
    if arguments.terms:
        added = transcripts.learn(arguments.terms, arguments.project, arguments.kind)
        print(f"{added} of {len(arguments.terms)} terms added")
        return None
    listed = transcripts.terms(arguments.project)
    misheard = upkeep.known(arguments.project)
    for term in listed:
        print(f"  {term}" + (f"  (heard as: {', '.join(misheard[term])})" if term in misheard else ""))
    print(f"{len(listed)} terms")
    return None


def run_mend(arguments: argparse.Namespace) -> int | None:
    from ..audio import upkeep

    if arguments.undo:
        if arguments.dry_run:
            print("media: --undo with --dry-run would do nothing: leave one out", file=sys.stderr)
            return 2
        restored, dropped = upkeep.restore(arguments.project, arguments.match)
        print(f"{restored} lines put back to what the recogniser wrote, {dropped} corrections dropped")
        return None
    mended = upkeep.mend(arguments.project, arguments.match, keep=not arguments.dry_run)
    for line in mended:
        print(f"  {line.file}  {_clock(line.start_s)}  {', '.join(line.rules)}")
        print(f"      {line.before}")
        print(f"   -> {line.after}")
    files = len({line.file for line in mended})
    if arguments.dry_run:
        print(f"{len(mended)} lines in {files} recordings would be mended; nothing was written")
    elif mended:
        print(f"{len(mended)} lines mended in {files} recordings; `media passages` draws their passages "
              "again, and `media mend --undo` puts them back")
    else:
        print("0 lines mended: no recorded mishearing is in any transcript")
    return None


def run_relisten(arguments: argparse.Namespace) -> None:
    from ..audio import upkeep
    from ..transcription import transcriber

    listener = transcriber(arguments.adapter)
    how = {key: value for key, value in (
        ("unsure", arguments.below), ("sparse", arguments.sparse), ("min_length_s", arguments.min_length),
    ) if value is not None}
    if arguments.dry_run:
        weak = upkeep.weakest(arguments.project, arguments.match, model=listener.model,
                              limit=arguments.limit, **how)
        for _, path, _, _, model, sure, segments in weak:
            print(f"  {_sure(sure)}  {segments:>5} segments  {path.rsplit('/', 1)[-1]}  ({model or 'model not known'})")
        print(f"{len(weak)} recordings would be listened to again with {listener.name} ({listener.model})")
        return
    done = upkeep.relisten(
        arguments.project, arguments.match, language=arguments.language,
        vocabulary=not arguments.no_vocabulary, limit=arguments.limit, transcriber=listener, **how,
    )
    for heard in done:
        print(f"  {'replaced' if heard.better else 'kept    '}  {heard.file}  "
              f"{_sure(heard.was_sure)}, {heard.was_segments} segments -> "
              f"{_sure(heard.sure)}, {heard.segments} segments")
    replaced = sum(heard.better for heard in done)
    print(f"{len(done)} recordings listened to again with {listener.name} ({listener.model}): "
          f"{replaced} replaced, {len(done) - replaced} kept as they were")
    if replaced:
        print("a replaced transcript has no voices or passages yet: `media diarize`, `media passages`")


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _sure(sure: float | None) -> str:
    return "sureness not given" if sure is None else f"sureness {sure:+.2f}"


def run_diarize(arguments: argparse.Namespace) -> None:
    import contextlib

    from .. import db
    from ..speakers import cards, embedder, voices

    listener = embedder(arguments.adapter)
    looking = arguments.dry_run
    # Each recording is stored as it is done, so one that fails loses only itself. Looking stores none of them.
    with db.transaction(keep=False) if looking else contextlib.nullcontext():
        done = voices.find(
            arguments.match, voices=arguments.voices, alike=arguments.alike, force=arguments.force,
            embedder=listener,
        )
    for found in done:
        if not (arguments.no_cards or looking):
            cards.make(found.asset_id, found.source, found.telling.voices)
        print(f"  {found.name}")
        for voice in found.telling.voices:
            _voice(voice.label, voice.segments, voice.speech_s, voice.cohesion, voice.persona, voice.like,
                   None if looking else cards.card(found.asset_id, voice.label))
        if found.telling.unknown:
            print(f"    {found.telling.unknown} segments left unknown: too short or too unclear to tell")
    print(f"{len(done)} recordings gone over with {listener.name} ({listener.model}): "
          f"{sum(len(found.telling.voices) for found in done)} voices"
          + ("; nothing was stored" if looking else ""))


def run_speakers(arguments: argparse.Namespace) -> None:
    from ..speakers import cards, names

    recordings = names.listed(arguments.match)
    if arguments.people:
        spoken: dict[str, list[float]] = {}
        for recording in recordings:
            for person in {voice.persona for voice in recording.voices if voice.persona}:
                theirs = [voice for voice in recording.voices if voice.persona == person]
                total = spoken.setdefault(person, [0, 0, 0.0])
                total[0] += 1
                total[1] += sum(voice.segments for voice in theirs)
                total[2] += sum(voice.speech_s for voice in theirs)
        for person, (within, lines, seconds) in sorted(spoken.items(), key=lambda item: (-item[1][2], item[0])):
            minutes, rest = divmod(round(seconds), 60)
            print(f"  {person:<24} {lines:>6} segments  {minutes:>4}:{rest:02d}  in {within} "
                  f"recording{'' if within == 1 else 's'}")
        unnamed = sum(voice.persona is None for recording in recordings for voice in recording.voices)
        print(f"{len(spoken)} people in {len(recordings)} recordings, {unnamed} voices without a name")
        return
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
