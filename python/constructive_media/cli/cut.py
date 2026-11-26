"""Commands for cutting: compile, timelines, note, keep, cut, prefer, pairs, script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .analyze import MATCH_HELP


def register(commands: Any) -> None:
    command = commands.add_parser(
        "compile", help="choose shots that are good and unalike, and store them as a cut",
        description="Pick shots by a recipe's ranking, each one for its quality less how much it looks "
                    "like those already picked, and order them so neighbours differ. Made --after "
                    "another cut, it keeps the clips that were kept or adjusted where they were, "
                    "replaces the ones that were dropped, and takes that cut's settings.",
    )
    command.add_argument("name", help="what to call the cut")
    command.add_argument("--using", metavar="SELECTOR", help="take the settings from a selector")
    command.add_argument("--after", metavar="TIMELINE", help="revise this cut, honouring what was said about it")
    command.add_argument("--recipe", help="the recipe that says what a good shot is (default broll)")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--project", help="only this project's footage")
    command.add_argument("--count", type=int, metavar="N", help="how many clips (default 12)")
    command.add_argument("--diversity", type=float, metavar="0-1",
                         help="how much looking alike costs against quality (default 0.5)")
    command.add_argument("--excerpt", type=float, metavar="SECONDS",
                         help="use this much of each shot, around its sharpest moment (default: all of it)")
    command.add_argument("--min-length", type=float, metavar="SECONDS",
                         help="the shortest shot to consider (default 1)")
    command.add_argument("--chronological", action=argparse.BooleanOptionalAction,
                         help="order by file and time, not by look")
    command.add_argument("--people", action=argparse.BooleanOptionalAction,
                         help="only shots with somebody in them")
    command.add_argument("--seed", type=int, help="make a variant: the same seed gives the same cut")
    command.add_argument("--episode", help="the episode this cut is for, whose own cuts it then honours")
    command.add_argument("--title", help="a title for the cut")
    command.add_argument("--remember", metavar="SELECTOR", help="also keep these settings as a selector")
    command.add_argument("--dry-run", action="store_true", help="show the cut and store nothing")
    command.set_defaults(run=run_compile)

    command = commands.add_parser("selectors", help="list the kept sets of selection settings")
    command.set_defaults(run=run_selectors)

    command = commands.add_parser("timelines", help="list the cuts")
    command.add_argument("--project", help="only this project's")
    command.add_argument("--episode", help="only this episode's")
    command.set_defaults(run=run_timelines)

    command = commands.add_parser(
        "timeline", help="show a cut clip by clip, with what was said about each",
        description="Show a cut clip by clip, with the last thing said about each. With --status, "
                    "also say where the cut stands.",
    )
    command.add_argument("name", help="the cut")
    command.add_argument("--status", choices=("draft", "review", "approved", "discarded"),
                         help="mark the cut as this")
    command.set_defaults(run=run_timeline)

    command = commands.add_parser(
        "note", help="say what you made of one clip of a cut",
        description="React to one clip. The next cut made --after this one keeps what was kept, "
                    "moves what was adjusted, and replaces what was dropped.",
    )
    command.add_argument("timeline", help="the cut")
    command.add_argument("clip", type=int, help="the clip's number, from 1")
    command.add_argument("verdict", choices=("keep", "drop", "adjust"))
    command.add_argument("--shift", type=float, metavar="SECONDS", help="adjust: start this much later (or earlier, negative)")
    command.add_argument("--extend", type=float, metavar="SECONDS", help="adjust: run this much longer (or shorter, negative)")
    command.add_argument("--note", help="why, in your own words")
    command.add_argument("--never", action="store_true",
                         help="with drop: cut the shot from everything, not only from this line of cuts")
    command.set_defaults(run=run_note)

    for name, summary in (("keep", "rule that a passage or shot is to be used, lifting any cut"),
                          ("cut", "rule a passage or shot out of every ranking and cut")):
        command = commands.add_parser(name, help=summary)
        command.add_argument("id", help="the start of a passage's or shot's id, as listed")
        command.add_argument("--note", help="why, in your own words")
        command.add_argument("--episode", help="only as far as this episode goes")
        command.set_defaults(run=run_keep if name == "keep" else run_cut)

    command = commands.add_parser(
        "prefer", help="rule that one passage or shot is better than another",
        description="Record which of two you would rather use. A ranker is taught from these.",
    )
    command.add_argument("chosen", help="the id of the one you would use")
    command.add_argument("rejected", help="the id of the other")
    command.add_argument("--why", help="what decided it")
    command.set_defaults(run=run_prefer)

    command = commands.add_parser(
        "pairs", help="list pairs most worth choosing between",
        description="List pairs a recipe cannot tell apart, or with --mode contrast, shots it scores "
                    "alike that look different. Answer with `me prefer`.",
    )
    command.add_argument("recipe", nargs="?", default="broll", help="the recipe to go by (default %(default)s)")
    command.add_argument("--mode", choices=("close", "contrast"), default="close")
    command.add_argument("--count", type=int, default=10, metavar="N", help="how many pairs (default %(default)s)")
    command.add_argument("--project", help="only this project's")
    command.set_defaults(run=run_pairs)

    command = commands.add_parser("decisions", help="count the rulings made, and show the latest")
    command.add_argument("--recent", type=int, default=10, metavar="N", help="how many to show (default %(default)s)")
    command.set_defaults(run=run_decisions)

    command = commands.add_parser(
        "script", help="write the best passages out to mark up, or read a marked-up script back",
        description="Write the best passages by a recipe as a document, in the order they were said, "
                    "with a line under each to mark keep, cut, trim or ?. With --read, store the "
                    "marks in a document as decisions.",
    )
    command.add_argument("recipe", nargs="?", default="interview", help="the recipe to rank by (default %(default)s)")
    command.add_argument("--take", help="only this take's passages")
    command.add_argument("--project", help="only this project's")
    command.add_argument("--episode", help="the episode this is for: its cuts are left out, and cuts read are from it")
    command.add_argument("--top", type=int, default=40, metavar="N", help="how many passages (default %(default)s)")
    command.add_argument("--method", help="only passages found this way")
    command.add_argument("--out", type=Path, metavar="PATH", help="write to a file, not to the screen")
    command.add_argument("--read", type=Path, metavar="PATH", help="read this marked-up script back")
    command.add_argument("--dry-run", action="store_true", help="with --read: show what would be stored and store nothing")
    command.set_defaults(run=run_script)


def _short(subject_id: Any) -> str:
    """As much of an id as is shown, and enough to give back."""
    return str(subject_id).replace("-", "")[:12]


def _show(clips: list[Any], said: dict[int, Any] | None = None) -> None:
    from ..edit import clock

    for index, clip in enumerate(clips, 1):
        line = (f"  {index:>3}  {clock(clip.start_s)}-{clock(clip.end_s)}  ({clip.end_s - clip.start_s:.1f}s)  "
                f"{clip.label or clip.picture}")
        if said and (reaction := said.get(index)):
            moved = "".join(f" {name} {amount:+g}s" for name, amount in
                            (("shift", reaction.shift_s), ("extend", reaction.extend_s)) if amount)
            line += f"  [{reaction.verdict}{moved}" + (f": {reaction.note}" if reaction.note else "") + "]"
        print(line)


def run_compile(arguments: argparse.Namespace) -> None:
    from .. import db
    from ..edit import clock, select

    with db.transaction(keep=not arguments.dry_run):
        made = select.compilation(
            arguments.name, using=arguments.using, after=arguments.after, seed=arguments.seed,
            episode=arguments.episode, title=arguments.title, recipe=arguments.recipe, match=arguments.match,
            project=arguments.project, count=arguments.count, diversity=arguments.diversity,
            excerpt_s=arguments.excerpt, min_length_s=arguments.min_length,
            chronological=arguments.chronological, people=arguments.people,
        )
        if arguments.remember:
            select.remember(arguments.remember, made.options)
    cut, chosen = made.timeline, made.selection
    _show(cut.clips)
    print(f"{len(cut.clips)} clips, {clock(cut.duration_s)}, from {chosen.considered} shots by {made.options.recipe}"
          + (f"; revision {cut.revision}, after {cut.after}" if cut.after else ""))
    if chosen.likeness is not None:
        print(f"neighbours are {chosen.likeness:.2f} alike"
              + (f"; the top {made.options.count} as they come would be {chosen.unselected:.2f}"
                 if chosen.unselected is not None else ""))
    if chosen.held:
        print(f"{chosen.held} carried over from {cut.after}")
    if chosen.unsigned:
        print(f"{chosen.unsigned} shots have no signature and were left out; `me signatures` gives them one")
    if len(cut.clips) < made.options.count:
        print(f"{made.options.count} were asked for and there were not that many to choose from")
    if arguments.dry_run:
        print("nothing was written")


def run_selectors(_arguments: argparse.Namespace) -> None:
    from dataclasses import asdict

    from ..edit import select

    for kept in select.selectors():
        plain = asdict(select.Options())
        settings = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in asdict(kept.options).items()
                             if value != plain[key])
        print(f"{kept.name}: {settings or 'the defaults'}" + (f". {kept.description}" if kept.description else ""))


def run_timelines(arguments: argparse.Namespace) -> None:
    from ..edit import clock, timelines

    found = timelines.listing(arguments.project, arguments.episode)
    for cut in found:
        said = ", ".join(f"{count} {what}" for count, what in ((cut.dropped, "dropped"), (cut.adjusted, "adjusted"))
                         if count)
        print(f"  {cut.name}  {cut.kind}  {cut.clips} clips  {clock(cut.duration_s)}  {cut.status}"
              + (f"  revision {cut.revision}" if cut.revision > 1 else "")
              + (f"  for {cut.episode}" if cut.episode else "") + (f"  ({said})" if said else ""))
    print(f"{len(found)} timelines")


def run_timeline(arguments: argparse.Namespace) -> None:
    from ..edit import clock, timelines

    if arguments.status:
        timelines.mark(arguments.name, arguments.status)
    cut = timelines.load(arguments.name)
    _show(cut.clips, timelines.reactions(cut.name))
    print(f"{cut.name}: {cut.kind}, {len(cut.clips)} clips, {clock(cut.duration_s)}, {cut.status}"
          + (f", revision {cut.revision} after {cut.after}" if cut.after else "")
          + (f", for {cut.episode}" if cut.episode else "") + (f", seed {cut.seed}" if cut.seed is not None else ""))


def run_note(arguments: argparse.Namespace) -> None:
    from .. import db
    from ..edit import EditError, decisions, timelines

    if arguments.never and arguments.verdict != "drop":
        raise EditError("--never goes with drop")
    with db.transaction():
        clip = timelines.react(arguments.timeline, arguments.clip, arguments.verdict, shift_s=arguments.shift,
                               extend_s=arguments.extend, note=arguments.note)
        if arguments.never:
            if clip.shot_id is None:
                raise EditError(f"clip {arguments.clip} is not a shot, so there is nothing to cut from everything")
            decisions.cut(decisions.find(str(clip.shot_id), "shot"), note=arguments.note)
    print(f"clip {arguments.clip} of {arguments.timeline}: {arguments.verdict}"
          + ("; its shot is cut from everything" if arguments.never else ""))


def run_keep(arguments: argparse.Namespace) -> None:
    from ..edit import decisions

    subject = decisions.find(arguments.id)
    changed = decisions.keep(subject, note=arguments.note, episode=arguments.episode)
    print(f"{'kept' if changed else 'already kept'}: {subject}")


def run_cut(arguments: argparse.Namespace) -> None:
    from ..edit import decisions

    subject = decisions.find(arguments.id)
    changed = decisions.cut(subject, note=arguments.note, episode=arguments.episode)
    print(f"{'cut' if changed else 'already cut'}"
          + (f" from {arguments.episode}" if arguments.episode else "") + f": {subject}")


def run_prefer(arguments: argparse.Namespace) -> None:
    from ..edit import decisions

    chosen, rejected = decisions.find(arguments.chosen), decisions.find(arguments.rejected)
    changed = decisions.prefer(chosen, rejected, why=arguments.why)
    print(f"{'preferred' if changed else 'already preferred'}: {chosen} over {rejected}")


def run_pairs(arguments: argparse.Namespace) -> None:
    from ..edit import decisions

    found = decisions.pairs(arguments.recipe, mode=arguments.mode, count=arguments.count, project=arguments.project)
    for pair in found:
        alike = f", {pair.likeness:.2f} alike" if pair.likeness is not None else ""
        print(f"  {_short(pair.one.id)}  {pair.one.where}")
        print(f"  {_short(pair.other.id)}  {pair.other.where}  ({pair.gap:.3f} apart in score{alike})")
        print()
    print(f"{len(found)} pairs; answer each with `me prefer <chosen> <rejected>`")


def run_decisions(arguments: argparse.Namespace) -> None:
    from ..edit import decisions

    counted = decisions.tally()
    for (kind, source), count in counted.items():
        print(f"  {count:>5}  {kind.replace('_', ' ')}  ({source})")
    print(f"{sum(counted.values())} decisions")
    for ruling in decisions.recent(arguments.recent):
        verdict = {True: "keep", False: "cut", None: "note"}[ruling.keep] if ruling.over is None else "prefer"
        print(f"  {ruling.when:%Y-%m-%d %H:%M}  {ruling.actor}  {verdict}  {_short(ruling.subject)}"
              + (f" over {_short(ruling.over)}" if ruling.over else "") + (f"  {ruling.note}" if ruling.note else ""))


def run_script(arguments: argparse.Namespace) -> int | None:
    from .. import db
    from ..edit import script

    if arguments.read is None:
        text = script.write(arguments.recipe, take=arguments.take, project=arguments.project,
                            episode=arguments.episode, limit=arguments.top, method=arguments.method)
        if arguments.out is None:
            sys.stdout.write(text)
        else:
            arguments.out.write_text(text, encoding="utf-8")
            print(f"{text.count(chr(10) + '## ')} passages written to {arguments.out}")
        return None
    try:
        text = arguments.read.read_text(encoding="utf-8")
    except OSError as error:
        print(f"me: {arguments.read} cannot be read: {error.strerror}", file=sys.stderr)
        return 1
    with db.transaction(keep=not arguments.dry_run):
        done = script.read(text, episode=arguments.episode)
    print(f"{done.kept} kept, {done.cut} cut, {done.noted} noted; {done.unchanged} already on record, "
          f"{done.unmarked} left unmarked" + ("; nothing was written" if arguments.dry_run else ""))
    for passage in done.unknown:
        print(f"  {passage}: not one passage's id, so its mark was not stored")
    for passage, said in done.unreadable:
        print(f"  {passage}: {said!r} is not keep, cut, trim or ?")
    return 1 if done.unknown or done.unreadable else None
