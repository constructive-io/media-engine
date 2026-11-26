"""Commands for finding what is worth using: passages, flag, score and rank."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .analyze import MATCH_HELP


def register(commands: Any) -> None:
    command = commands.add_parser(
        "passages", help="find the stretches of each take that stand on their own",
        description="Cut each take's speech into passages: by silence, which finds the exchanges "
                    "(a question and its answer), or by sentence, which finds the lines to quote. "
                    "Running it again finds the same passages, not more of them. Ones it no longer "
                    "finds are removed, unless a decision has been made about them.",
    )
    command.add_argument("takes", nargs="*", metavar="take", help="a take to cut up (default: all of them)")
    command.add_argument("--project", help="only this project's takes")
    command.add_argument("--by", choices=("silence", "sentence"), default="silence",
                         help="where to cut (default %(default)s)")
    command.add_argument("--min-gap", type=float, metavar="SECONDS",
                         help="by silence: the shortest quiet that ends a passage (default 1.2)")
    command.add_argument("--min-length", type=float, metavar="SECONDS",
                         help="the shortest passage to keep (default 12 by silence, 1.2 by sentence)")
    command.add_argument("--max-length", type=float, metavar="SECONDS",
                         help="the longest passage to leave whole (default 90 by silence, 12 by sentence)")
    command.add_argument("--name", help="store these under a name of their own, to compare settings")
    command.add_argument("--dry-run", action="store_true", help="show what would be stored and store nothing")
    command.set_defaults(run=run_passages)

    command = commands.add_parser(
        "flag", help="mark what to cut, look at or keep away from in each transcript",
        description="Read each transcript for fillers, stutters, sentences started twice, dead air, "
                    "lines the recogniser was guessing at, and anyone asking for something not to be "
                    "used. A flag you have accepted or dismissed is left as it is.",
    )
    command.add_argument("--match", help=MATCH_HELP)
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--min-pause", type=float, default=1.5, metavar="SECONDS",
                         help="the shortest quiet to call dead air (default %(default)g)")
    command.set_defaults(run=run_flag)

    command = commands.add_parser(
        "score", help="score every passage and shot from what has been measured so far",
        description="Write down what is known about each passage and shot as scores from 0 to 1: from "
                    "the words, from how sure the recogniser was, from the picture, and from the flags, "
                    "which are redone first. Run it again after any analysis to take that in.",
    )
    command.add_argument("--project", help="only this project's takes and footage")
    command.add_argument("--min-pause", type=float, default=1.5, metavar="SECONDS",
                         help="the shortest quiet to flag as dead air (default %(default)g)")
    command.set_defaults(run=run_score)

    command = commands.add_parser(
        "rank", help="list the best passages or shots by a recipe, or list the recipes",
        description="With a recipe, list the best of what it ranks. With none, list the recipes. A "
                    "passage in which someone asks for it not to be used is never listed.",
    )
    command.add_argument("recipe", nargs="?", help="the recipe to rank by")
    command.add_argument("--project", help="only this project's")
    command.add_argument("--top", type=int, default=25, metavar="N", help="how many to list (default %(default)s)")
    command.add_argument("--min-coverage", type=float, default=0.5, metavar="SHARE",
                         help="how much of the recipe must have been measured, 0 to 1 (default %(default)g)")
    command.add_argument("--method", help="only passages found this way: gap, sentence, or a --name you gave")
    command.set_defaults(run=run_rank)

    command = commands.add_parser(
        "recipe", help="show a recipe, make one, or change what one weighs",
        description="A recipe says what a good passage or shot is: which scores count, and by how much "
                    "beside each other. With only a name, show it. `--weigh preferred=0.5` on a recipe "
                    "for shots makes it rank by what a fitted model learned you prefer.",
    )
    command.add_argument("name", help="the recipe")
    command.add_argument("--like", metavar="RECIPE", help="start from this recipe's weights")
    command.add_argument("--weigh", action="append", default=[], metavar="SCORE=WEIGHT",
                         help="how much a score counts; 0 takes it out. May be given more than once")
    command.add_argument("--ranks", choices=("passage", "shot"), help="what it ranks (default shot)")
    command.add_argument("--needs", type=int, metavar="N",
                         help="how many senses must have a score before a thing ranks")
    command.add_argument("--describe", metavar="TEXT", help="what the recipe is for")
    command.set_defaults(run=run_recipe)


def run_passages(arguments: argparse.Namespace) -> None:
    from .. import db, takes
    from ..edit import passages

    options = {key: value for key, value in (
        ("min_gap_s", arguments.min_gap), ("min_len_s", arguments.min_length),
        ("max_len_s", arguments.max_length)) if value is not None}
    chosen = [takes.find(name) for name in arguments.takes] or takes.listing(arguments.project)
    total = 0
    with db.transaction(keep=not arguments.dry_run):
        for take in chosen:
            done = passages.propose(take, by=arguments.by, name=arguments.name, **options)
            changes = [f"{count} {what}" for count, what in (
                (done.added, "new"), (done.removed, "removed"), (done.held, "kept for the decisions on them"),
            ) if count]
            print(f"  {done.passages:>5} passages  {take.name}" + (f"  ({', '.join(changes)})" if changes else ""))
            total += done.passages
    print(f"{total} passages in {len(chosen)} takes" + ("; nothing was written" if arguments.dry_run else ""))


def run_flag(arguments: argparse.Namespace) -> None:
    from ..edit import flags

    found = flags.scan(arguments.match, arguments.project, min_pause_s=arguments.min_pause)
    for kind in flags.KINDS:
        if found[kind]:
            print(f"  {found[kind]:>5}  {kind.replace('_', ' ')}")
    print(f"{sum(found.values())} flags")


def run_score(arguments: argparse.Namespace) -> None:
    from ..edit import ranker, scores

    written = scores.run(arguments.project, min_pause_s=arguments.min_pause) + ranker.refresh()
    for name, count in sorted(written.items()):
        print(f"  {count:>5}  {name.replace('_', ' ')}")
    print(f"{sum(written.values())} scores")


def _recipe(recipe: Any) -> None:
    weights = ", ".join(f"{name.replace('_', ' ')} {weight:g}" for name, weight in
                        sorted(recipe.weights.items(), key=lambda item: -item[1]))
    print(f"{recipe.name}: ranks {recipe.subject}s. {recipe.description}".rstrip())
    print(f"  {weights}")


def run_recipe(arguments: argparse.Namespace) -> int | None:
    from ..edit import EditError, scores

    existing = {recipe.name: recipe for recipe in scores.recipes()}
    changing = arguments.like or arguments.weigh or arguments.ranks or arguments.needs or arguments.describe
    if not changing:
        _recipe(scores.recipe(arguments.name))
        return None
    start = scores.recipe(arguments.like) if arguments.like else existing.get(arguments.name)
    weights = dict(start.weights) if start else {}
    for given in arguments.weigh:
        score, _, weight = given.partition("=")
        try:
            weights[score.strip().replace(" ", "_")] = float(weight)
        except ValueError:
            raise EditError(f"--weigh takes SCORE=WEIGHT, as in preferred=0.5, not {given!r}") from None
    recipe = scores.define(
        arguments.name, {score: weight for score, weight in weights.items() if weight},
        subject=arguments.ranks or (start.subject if start else "shot"),
        description=arguments.describe if arguments.describe is not None else (
            existing[arguments.name].description if arguments.name in existing else ""),
        min_modalities=arguments.needs or (start.min_modalities if start else 1),
    )
    _recipe(recipe)
    if unknown := sorted(set(recipe.weights) - scores.known(recipe.subject)):
        print(f"no {recipe.subject} has a score called {', '.join(unknown)} yet, so for now it counts for nothing")
    return None


def run_rank(arguments: argparse.Namespace) -> None:
    from ..edit import clock, scores

    if not arguments.recipe:
        for recipe in scores.recipes():
            _recipe(recipe)
        return
    ranking = scores.ranked(arguments.recipe, limit=arguments.top, min_coverage=arguments.min_coverage,
                            project=arguments.project, method=arguments.method)
    for entry in ranking.entries:
        source = entry.source if ranking.recipe.subject == "passage" else Path(entry.source).name
        text = entry.text if len(entry.text) <= 72 else entry.text[:71].rstrip() + "…"
        print(f"  {entry.score:.2f}  {source}  {clock(entry.start_s)}-{clock(entry.end_s)}  "
              f"{entry.coverage:.0%} measured" + (f"  {text}" if text else ""))
    print(f"{len(ranking.entries)} {ranking.recipe.subject}s by {ranking.recipe.name}")
    if ranking.withheld:
        print(f"{ranking.withheld} left out: someone in them asks for it not to be used")
