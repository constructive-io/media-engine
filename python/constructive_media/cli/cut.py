"""Commands for cutting: compile, timelines, note, keep, cut, prefer, pairs, script, render, export."""

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

    command = commands.add_parser(
        "assemble", help="cut an interview from its best passages, with the best sound for each",
        description="Fill a cut with passages, best first by a recipe, after the ones you kept. Each is "
                    "seen through a camera that was rolling and heard through the microphone that heard "
                    "it best. Made --after another cut, it keeps the clips that were kept or adjusted, "
                    "leaves out what was dropped, and takes that cut's settings.",
    )
    command.add_argument("name", help="what to call the cut")
    command.add_argument("--take", help="only this take's passages")
    command.add_argument("--project", help="only this project's")
    command.add_argument("--episode", help="the episode this cut is for: what was cut from it is left out")
    command.add_argument("--passage", action="append", metavar="ID",
                         help="use this passage, and only the ones named this way, in the order given")
    command.add_argument("--after", metavar="TIMELINE", help="revise this cut, honouring what was said about it")
    command.add_argument("--style", help="the manner to cut in (`media styles` lists them)")
    command.add_argument("--target", type=float, metavar="SECONDS", help="how long the cut is to be (default 300)")
    command.add_argument("--min-length", type=float, metavar="SECONDS", help="the shortest passage to use (default 12)")
    command.add_argument("--max-length", type=float, metavar="SECONDS", help="the longest passage to use (default 95)")
    command.add_argument("--min-words", type=int, metavar="N", help="the fewest words a passage may have (default 80)")
    command.add_argument("--quiet", type=float, metavar="SECONDS",
                         help="the most quiet to leave at either end of a passage (default 1.5)")
    command.add_argument("--chronological", action=argparse.BooleanOptionalAction,
                         help="in the order it was said, not best first (default: in order)")
    command.add_argument("--recipe", help="the recipe that says what a good passage is (default interview)")
    command.add_argument("--method", help="only passages found this way")
    command.add_argument("--per-take", type=int, metavar="N",
                         help="the most passages from any one take, when there are several takes")
    command.add_argument("--title", help="a title for the cut")
    command.add_argument("--dry-run", action="store_true", help="show the cut and store nothing")
    command.set_defaults(run=run_assemble)

    command = commands.add_parser(
        "cutaways", help="lay other footage over parts of a cut while its sound carries on",
        description="Cover some of a cut's clips with shots ranked by a recipe, each the best that is "
                    "least like the one before. The sound is not touched. Shots with somebody filling "
                    "the frame are left out, and so is footage from the takes the cut is made from.",
    )
    command.add_argument("name", help="what to call the cut with the footage over it")
    command.add_argument("--over", required=True, metavar="TIMELINE", help="the cut to lay footage over")
    command.add_argument("--fraction", type=float, metavar="0-1",
                         help="how many of the clips to cover (default: what the cut's style says, or 0.25)")
    command.add_argument("--recipe", default="broll", help="the recipe that says what a good shot is (default %(default)s)")
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--project", help="only this project's footage")
    command.add_argument("--shortest", type=float, default=2.5, metavar="SECONDS",
                         help="the shortest cutaway worth making (default %(default)s)")
    command.add_argument("--longest", type=float, default=6.0, metavar="SECONDS",
                         help="the longest, and the longest clip to cover whole (default %(default)s)")
    command.add_argument("--lead", type=float, default=2.0, metavar="SECONDS",
                         help="how long the speaker is seen at each end of a longer clip (default %(default)s)")
    command.add_argument("--diversity", type=float, default=0.5, metavar="0-1",
                         help="how much looking like the cutaway before costs against quality (default %(default)s)")
    command.add_argument("--title", help="a title for the cut")
    command.add_argument("--dry-run", action="store_true", help="show the cut and store nothing")
    command.set_defaults(run=run_cutaways)

    command = commands.add_parser("styles", help="list the manners a cut can be made in")
    command.set_defaults(run=run_styles)

    command = commands.add_parser(
        "fit", help="teach a model what you prefer from the choices you have made between shots",
        description="Fit a model to the choices made with `media prefer`, and test it on footage it was not "
                    "fitted to. With --save it is stored, and if it proved better than a coin, every shot "
                    "it covers gets a `preferred` score that a recipe can weigh (`media recipe`).",
    )
    command.add_argument("--save", metavar="NAME", help="store the model under this name, in place of any before")
    command.add_argument("--match", help="only choices between shots of files whose path matches; "
                                         "the model then scores only such shots")
    command.add_argument("--actor", help="only this person's choices")
    command.add_argument("--source", default="human", help="whose choices: human, or all (default %(default)s)")
    command.add_argument("--scope", help="what the model is for, in a few words")
    command.add_argument("--min-pairs", type=int, default=30, metavar="N",
                         help="refuse to fit to fewer choices than this (default %(default)s)")
    command.set_defaults(run=run_fit)

    command = commands.add_parser(
        "models", help="list the fitted models, or show one",
        description="List the fitted models with how each did on footage held out from it. With a name, "
                    "show what that model goes by.",
    )
    command.add_argument("name", nargs="?", help="the model to show")
    command.add_argument("--forget", action="store_true", help="delete the model and the scores it wrote")
    command.set_defaults(run=run_models)

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
                    "alike that look different. Answer with `media prefer`.",
    )
    command.add_argument("recipe", nargs="?", default="broll", help="the recipe to go by (default %(default)s)")
    command.add_argument("--mode", choices=("close", "contrast"), default="close")
    command.add_argument("--count", type=int, default=10, metavar="N", help="how many pairs (default %(default)s)")
    command.add_argument("--project", help="only this project's")
    command.add_argument("--render", metavar="FOLDER",
                         help="also write each pair of shots as a video: the first on the left, the second "
                              "on the right")
    command.add_argument("--seconds", type=float, default=4.0, metavar="N",
                         help="with --render: how long each video runs (default %(default)s)")
    command.set_defaults(run=run_pairs)

    command = commands.add_parser(
        "render", help="make a cut into a file to watch",
        description="Read a cut's clips out of their recordings into one file, every clip fitted to "
                    "the cut's canvas, the sound brought to one loudness and unbroken where clips "
                    "carry on the same recording. The file is then looked at and what was found is "
                    "stored with what it was made from.",
    )
    command.add_argument("name", help="the cut")
    command.add_argument("--out", metavar="FILE", help="where to write it (default: NAME.mp4 in the work folder)")
    command.add_argument("--canvas", choices=("landscape", "vertical", "square"),
                         help="the shape to render to (default: the cut's own)")
    command.add_argument("--fit", choices=("pad", "crop"),
                         help="show all of each picture with bars, or fill the canvas and lose the edges "
                              "(default: the cut's own)")
    command.add_argument("--height", type=int, metavar="PIXELS", help="how tall (default: 540, or 1080 vertical, 720 square)")
    command.add_argument("--fps", type=float, metavar="N", help="frames a second (default: what most of the cut was shot at)")
    command.add_argument("--originals", action="store_true",
                         help="read the recordings themselves, not the small copies made of them")
    command.add_argument("--silent", action="store_true", help="picture only")
    command.add_argument("--loudness", type=float, default=-16.0, metavar="LUFS",
                         help="how loud to make the sound (default %(default)s)")
    command.add_argument("--as-recorded", action="store_true", help="leave the sound as loud as it was recorded")
    command.add_argument("--megabytes", type=float, metavar="N", help="make the file about this big, to send")
    command.set_defaults(run=run_render)

    command = commands.add_parser(
        "export", help="hand a cut to an editing program, pointed at the recordings themselves",
        description="Write a cut as FCPXML and as a CMX3600 edit decision list, with a page that says "
                    "what they point at. Both name the original recordings where they are kept, on each "
                    "recording's own timecode, and nothing is copied.",
    )
    command.add_argument("name", help="the cut to export")
    command.add_argument("--to", type=Path, metavar="FOLDER",
                         help="where to write (default: exports/NAME in the work folder)")
    command.add_argument("--fps", type=float, metavar="N",
                         help="frames a second (default: what most of the cut was shot at)")
    command.set_defaults(run=run_export)

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
        line = (f"  {index:>3}  {clock(clip.start_s)}-{clock(clip.end_s)}  ({clip.duration_s:.1f}s)  "
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
        print(f"{chosen.unsigned} shots have no signature and were left out; `media signatures` gives them one")
    if len(cut.clips) < made.options.count:
        print(f"{made.options.count} were asked for and there were not that many to choose from")
    if arguments.dry_run:
        print("nothing was written")


def run_assemble(arguments: argparse.Namespace) -> None:
    from collections import Counter
    from dataclasses import replace

    from .. import db
    from ..edit import assemble, clock

    shape = assemble.style(arguments.style) if arguments.style else None
    changes = {key: value for key, value in (
        ("target_s", arguments.target), ("min_passage_s", arguments.min_length),
        ("max_passage_s", arguments.max_length), ("min_words", arguments.min_words),
        ("strip_silence_over_s", arguments.quiet), ("chronological", arguments.chronological),
    ) if value is not None}
    if changes and shape is None and arguments.after:
        shape = assemble.shaped(arguments.after)
    if changes:
        shape = replace(shape or assemble.Style(), **changes)
    with db.transaction(keep=not arguments.dry_run):
        made = assemble.interview(
            arguments.name, shape, take=arguments.take, project=arguments.project, episode=arguments.episode,
            recipe=arguments.recipe, method=arguments.method, per_take=arguments.per_take,
            only=arguments.passage, after=arguments.after, title=arguments.title,
        )
    cut = made.timeline
    parts = {(str(part.clip.picture), round(part.clip.start_s, 3)): part for part in made.parts}
    for index, clip in enumerate(cut.clips, 1):
        part = parts.get((str(clip.picture), round(clip.start_s, 3)))
        seen = (f"{part.picture}{f' with {part.sound}' if part.sound else ''}"
                + (f"  {part.score:.2f}" if part.score is not None else "") + ("  kept" if part.kept else "")
                if part else f"from {cut.after}")
        print(f"  {index:>3}  {clock(clip.start_s)}-{clock(clip.end_s)}  ({clip.duration_s:.1f}s)  {seen}  "
              f"{clip.label or ''}".rstrip())
    style = made.timeline.params.get("style", {})
    print(f"{len(cut.clips)} clips, {clock(cut.duration_s)} of {clock(style.get('target_s', 0))}, from "
          f"{made.considered} passages" + (f" in the {cut.style} style" if cut.style else "")
          + (f"; revision {cut.revision}, after {cut.after}" if cut.after else ""))
    if made.held:
        print(f"{made.held} carried over from {cut.after}")
    if made.left:
        reasons = Counter(passed.why for passed in made.left)
        print(f"{len(made.left)} passed over: " + ", ".join(f"{count} {why}" for why, count in reasons.most_common()))
    if made.withheld:
        print(f"{made.withheld} left out: someone in them asks for it not to be used")
    if arguments.dry_run:
        print("nothing was written")


def run_cutaways(arguments: argparse.Namespace) -> None:
    from .. import db
    from ..edit import assemble, clock

    with db.transaction(keep=not arguments.dry_run):
        made = assemble.cutaways(
            arguments.name, arguments.over, fraction=arguments.fraction, recipe=arguments.recipe,
            match=arguments.match, project=arguments.project, shortest_s=arguments.shortest,
            longest_s=arguments.longest, lead_s=arguments.lead, diversity=arguments.diversity,
            title=arguments.title,
        )
    cut = made.timeline
    _show(cut.clips)
    print(f"{len(cut.clips)} clips, {clock(cut.duration_s)}; {made.covered} of {arguments.over}'s have other "
          f"footage over them, from {made.available} shots by {arguments.recipe}")
    if made.covered < made.wanted:
        print(f"{made.wanted} were to be covered, and there were not that many shots that fit")
    if made.likeness is not None:
        print(f"one cutaway is {made.likeness:.2f} like the next")
    if arguments.dry_run:
        print("nothing was written")


def run_styles(_arguments: argparse.Namespace) -> None:
    from ..edit import assemble, clock

    found = assemble.styles()
    for style in found:
        print(f"{style.name}: {clock(style.target_s)} of passages {style.min_passage_s:g} to "
              f"{style.max_passage_s:g}s long with {style.min_words} words or more, "
              f"{'in the order said' if style.chronological else 'best first'}, at most "
              f"{style.strip_silence_over_s:g}s of quiet at their ends, footage over "
              f"{style.broll_fraction:.0%} of the clips" + (f". {style.description}" if style.description else ""))
    print(f"{len(found)} styles")


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
              + (f"  {cut.style} style" if cut.style else "")
              + (f"  for {cut.episode}" if cut.episode else "") + (f"  ({said})" if said else ""))
    print(f"{len(found)} timelines")


def run_timeline(arguments: argparse.Namespace) -> None:
    from ..edit import clock, timelines

    if arguments.status:
        timelines.mark(arguments.name, arguments.status)
    cut = timelines.load(arguments.name)
    _show(cut.clips, timelines.reactions(cut.name))
    print(f"{cut.name}: {cut.kind}, {len(cut.clips)} clips, {clock(cut.duration_s)}, {cut.status}"
          + (f", {cut.style} style" if cut.style else "")
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
        if arguments.render and pair.one.kind == pair.other.kind == "shot":
            from ..edit import render

            written = render.compare(
                pair.one.id, pair.other.id,
                Path(arguments.render) / f"{_short(pair.one.id)}-{_short(pair.other.id)}.mp4",
                seconds=arguments.seconds)
            print(f"  {written}")
        print()
    print(f"{len(found)} pairs; answer each with `media prefer <chosen> <rejected>`")


def run_render(arguments: argparse.Namespace) -> int:
    from ..edit import clock, render
    from ..settings import Settings

    settings = Settings.from_env()
    out = arguments.out or settings.work_dir / "renders" / f"{arguments.name}.mp4"
    made = render.render(
        arguments.name, out, canvas=arguments.canvas, fit=arguments.fit, height=arguments.height,
        fps=arguments.fps, silent=arguments.silent, originals=arguments.originals,
        lufs=None if arguments.as_recorded else arguments.loudness, megabytes=arguments.megabytes,
        settings=settings,
    )
    print(f"{arguments.name}: {made.clips} clips, {clock(made.duration_s)}, {made.width}x{made.height} at "
          f"{float(made.rate):.5g} frames a second" + (f", by {made.encoder}" if made.encoder else ""))
    if arguments.silent:
        print("  no sound")
    elif arguments.as_recorded:
        print(f"  sound in {len(made.gains)} unbroken stretches, as loud as it was recorded")
    else:
        changed = [gain for gain in made.gains if gain]
        print(f"  sound in {len(made.gains)} unbroken stretches"
              + (f", moved by {min(changed):+.1f} to {max(changed):+.1f} dB" if changed else ", none moved"))
    sizes = ", ".join(f"{width}x{height}" for width, height in made.sizes)
    print(f"  {made.looked} frames looked at: {sizes or 'none could be read'}")
    wrong = []
    if not made.uniform:
        wrong.append(f"the picture is not {made.width}x{made.height} throughout")
    if abs(made.adrift_s) > 2 / float(made.rate):
        wrong.append(f"the file is {abs(made.adrift_s):.2f} s {'longer' if made.adrift_s > 0 else 'shorter'} "
                     "than the cut")
    print(made.path)
    for each in wrong:
        print(f"media: {each}", file=sys.stderr)
    return 1 if wrong else 0


def run_export(arguments: argparse.Namespace) -> int:
    from ..edit import clock, export
    from ..settings import Settings

    folder = arguments.to or Settings.from_env().work_dir / "exports" / arguments.name
    made = export.export(arguments.name, folder, fps=arguments.fps)
    print(f"{arguments.name}: {made.clips} clips, {clock(made.duration_s)}, at {float(made.rate):.5g} "
          f"frames a second, from {made.sources} recordings")
    if made.left_out:
        print(f"  too short for a frame, and left out: clip {', '.join(map(str, made.left_out))}")
    if made.mixed:
        print(f"  shot at {', '.join(f'{float(each):.5g}' for each in made.rates)} frames a second: "
              "check the last clip against its sound")
    for written in (made.fcpxml, made.edl, made.readme):
        print(written)
    if made.missing:
        print(f"media: {len(made.missing)} of the recordings it points at are not there: "
              + ", ".join(file.name for file in made.missing), file=sys.stderr)
    return 1 if made.missing else 0


def _tested(model: Any) -> str:
    if model.accuracy is None:
        return "could not be tested: no footage could be held out and leave enough to fit to"
    return (f"right about {model.accuracy:.0%} of {model.tested} choices over footage held out by "
            f"{model.held_out_by}; a coin gets 50%")


def _model(model: Any) -> None:
    print(f"  {_tested(model)}")
    print(f"  right about {model.fitted:.0%} of the {model.pairs} it was fitted to, which it had seen")
    for fold in model.folds:
        print(f"    {fold.accuracy:>4.0%} of {fold.pairs:>4}  without {fold.held_out}")
    print("  goes by, strongest first (+ more is preferred, - less is):")
    for feature, weight in model.weights:
        print(f"    {weight:+.2f}  {feature.replace('_', ' ')}")


def run_fit(arguments: argparse.Namespace) -> None:
    from .. import db
    from ..edit import ranker

    model = ranker.train(arguments.save or "unsaved", match=arguments.match, actor=arguments.actor,
                         source=None if arguments.source == "all" else arguments.source,
                         scope=arguments.scope, min_pairs=arguments.min_pairs)
    print(f"{model.pairs} choices")
    _model(model)
    if not arguments.save:
        print("nothing stored; --save NAME keeps it")
        return
    with db.transaction():
        ranker.save(model)
        scored = ranker.apply(model)
    if model.proven:
        print(f"stored as {model.name}; {scored} shots given a `{ranker.SCORE}` score")
    else:
        print(f"stored as {model.name}, and not used: it has not shown it does better than a coin "
              "by more than chance. More choices may change that")


def run_models(arguments: argparse.Namespace) -> None:
    from ..edit import EditError, ranker

    if arguments.name:
        model = ranker.load(arguments.name)
        if arguments.forget:
            ranker.forget(arguments.name)
            print(f"forgot {arguments.name}")
            return
        print(f"{model.name}: {model.scope or 'a model of which shot is preferred'}"
              + (f", for files matching {model.match}" if model.match else ""))
        _model(model)
        print("  in use" if model.proven else "  not in use: not shown to be better than a coin")
        return
    if arguments.forget:
        raise EditError("name the model to forget")
    found = ranker.listing()
    for model in found:
        print(f"  {model.name}  {'in use' if model.proven else 'not in use'}  {_tested(model)}")
    print(f"{len(found)} models" + ("" if found else "; `media fit --save NAME` makes one"))


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
        print(f"media: {arguments.read} cannot be read: {error.strerror}", file=sys.stderr)
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
