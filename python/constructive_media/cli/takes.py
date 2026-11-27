"""Commands for takes: group, match, sync and takes; and by hand, take, overlap and pull."""

from __future__ import annotations

import argparse
import math
import shlex
from pathlib import Path
from typing import Any


def _moment(text: str) -> float:
    """Seconds, or a time as a clock shows it: `754.25`, `12:34.2`, `1:02:03`."""
    bare = text.strip()
    try:
        parts = [float(part) for part in bare.lstrip("+-").split(":")]
    except ValueError:
        parts = []
    if not 1 <= len(parts) <= 3 or not all(math.isfinite(part) and part >= 0 for part in parts):
        raise argparse.ArgumentTypeError(f"{text!r} is not a time: write seconds, or minutes:seconds")
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return -total if bare.startswith("-") else total


def register(commands: Any) -> None:
    from ..takes import ROLES

    command = commands.add_parser(
        "group", help="put recordings that were rolling at the same moment into takes",
        description="Read when each recording says it was made, from its file name or from inside "
                    "the file, and put those that overlap into one take. Only ever adds: no "
                    "recording is moved and no take is renamed or removed.",
    )
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--prefix", default="take",
                         help="new takes are named PREFIX-01, PREFIX-02, ... (default %(default)s)")
    command.add_argument("--min-overlap", type=float, default=60.0, metavar="SECONDS",
                         help="how long two recordings must overlap to be one take (default %(default)g)")
    command.add_argument("--utc-offset", type=float, metavar="HOURS",
                         help="the time zone the recorders' clocks were set to, as hours from UTC, "
                              "for times read from file names (default: this computer's zone)")
    command.add_argument("--singles", action="store_true",
                         help="instead, give each transcribed recording that is still in no take "
                              "a take of its own")
    command.add_argument("--dry-run", action="store_true", help="show what would be done and do nothing")
    command.set_defaults(run=run_group)

    command = commands.add_parser(
        "match", help="find the camera each loose sound recording belongs with, by what is said",
        description="For transcribed sound recordings that are in no take: find the camera that "
                    "recorded the same speech, check the match by content, and add the recording "
                    "to that camera's take.",
    )
    command.add_argument("--project", help="only this project's recordings")
    command.add_argument("--prefix", default="take",
                         help="a take made for a camera that has none is named PREFIX-01, ... "
                              "(default %(default)s)")
    command.add_argument("--dry-run", action="store_true", help="show what would be done and do nothing")
    command.set_defaults(run=run_match)

    command = commands.add_parser(
        "sync", help="place every recording of a take on one timeline, and check the result",
        description="Work out how far each recording of a take is from the take's primary "
                    "recording: by the words they share, then by sound, then by the devices' "
                    "clocks. Every answer is checked by comparing what is said at the same moment. "
                    "Exits with status 1 if any recording fails that check.",
    )
    command.add_argument("takes", nargs="*", metavar="take", help="a take to sync (default: all of them)")
    command.add_argument("--project", help="only this project's takes")
    command.add_argument("--check", action="store_true",
                         help="only check the offsets already stored, changing nothing")
    command.add_argument("--no-sound", action="store_true",
                         help="do not fall back to comparing how the recordings sound")
    command.add_argument("--dry-run", action="store_true", help="show what would be stored and store nothing")
    command.set_defaults(run=run_sync)

    command = commands.add_parser("takes", help="list the takes, or show what is in one")
    command.add_argument("take", nargs="?", help="a take to show")
    command.add_argument("--project", help="only this project's takes")
    command.set_defaults(run=run_takes)

    command = commands.add_parser(
        "take", help="make a take by hand, or mend one: its recordings, its primary, an offset",
        description="Say outright what `group`, `match` and `sync` otherwise work out. A take that does "
                    "not exist is made, and needs --primary. A RECORDING is a label in the take, a path, "
                    "a file name, or a pattern that fits one recording. An offset set here is kept as "
                    "`manual`: `sync` never moves it, and places the other recordings around it. With "
                    "nothing to change, the take is shown.",
    )
    command.add_argument("name", help="the take")
    command.add_argument("--project", help="the project the take belongs to")
    command.add_argument("--primary", metavar="RECORDING[:ROLE[:LABEL]]",
                         help="the recording whose own time is take time. For a take that has one already, "
                              "every offset is carried over to the new one")
    command.add_argument("--add", action="append", default=[], metavar="RECORDING[:ROLE[:LABEL]]",
                         help="put a recording in the take, or change the role or label of one that is in it. "
                              f"ROLE is one of {', '.join(ROLES)} (default: camera for video, lav for sound); "
                              "LABEL is what it is called in the take (default: its file name). May be repeated")
    command.add_argument("--offset", action="append", default=[], nargs=2, metavar=("RECORDING", "SECONDS"),
                         help="set by hand the seconds to add to a recording's time to reach take time. "
                              "May be repeated")
    command.add_argument("--forget", action="append", default=[], metavar="RECORDING",
                         help="let go of a recording's offset, so `sync` places it afresh. May be repeated")
    command.add_argument("--remove", action="append", default=[], metavar="RECORDING",
                         help="take a recording out of the take. May be repeated")
    command.add_argument("--dry-run", action="store_true", help="show what would be done and do nothing")
    command.set_defaults(run=run_take)

    command = commands.add_parser(
        "overlap", help="show the sentences two recordings of a take both say: the evidence for its sync",
        description="For each recording of a take, find the sentences it shares with the take's primary and "
                    "show when each of the two says them, and the offset that one sentence alone implies. "
                    "The offset most sentences agree on is set beside the one the take has stored. Reads "
                    "transcripts only and changes nothing. Exits with status 1 when no two recordings "
                    "share enough to say.",
    )
    command.add_argument("take", help="the take")
    command.add_argument("recording", nargs="?", help="only this recording of it (default: every one)")
    command.add_argument("--against", metavar="RECORDING",
                         help="the recording to compare with (default: the take's primary)")
    command.add_argument("--min-alike", type=float, default=0.55, metavar="RATIO",
                         help="how alike two lines must be to be one sentence, 0 to 1 (default %(default)g)")
    command.add_argument("--tolerance", type=float, default=2.0, metavar="SECONDS",
                         help="how far apart the offsets two sentences imply may be and still agree "
                              "(default %(default)g)")
    command.add_argument("--show", type=int, default=12, metavar="N",
                         help="how many of the agreeing sentences to show, spread through the recording "
                              "(default %(default)s)")
    command.set_defaults(run=run_overlap)

    command = commands.add_parser(
        "pull", help="cut the same moment of a take from every recording of it, to listen to",
        description="Cut one stretch of take time out of each recording of a take, as sound, into a folder. "
                    "If the offsets are right every file says the same words from the same instant. Each "
                    "file is recorded with the offset it was cut under, so `media lineage` can answer for it. "
                    "Exits with status 1 when no recording was rolling then.",
    )
    command.add_argument("take", help="the take")
    command.add_argument("--at", type=_moment, required=True, metavar="TIME",
                         help="where the stretch begins, in take time: seconds, or minutes:seconds (12:34.2)")
    command.add_argument("--seconds", type=float, default=12.0, help="how long a stretch (default %(default)g)")
    command.add_argument("--to", metavar="FOLDER", help="where to put the files (default: pulls/ in the work folder)")
    command.set_defaults(run=run_pull)


def run_group(arguments: argparse.Namespace) -> None:
    from .. import db
    from ..takes import clock, group

    # One unit, so a dry run can read the recording times, show what they
    # lead to, and leave nothing behind.
    with db.transaction(keep=not arguments.dry_run):
        if arguments.singles:
            proposals = group.alone(arguments.project)
        else:
            read = clock.stamp(arguments.project, utc_offset_hours=arguments.utc_offset)
            if sum(read.values()):
                print(f"recording times read: {read['filename']} from file names, "
                      f"{read['container']} from inside the file, {read['unknown']} not found")
            proposals = group.by_clock(arguments.project, prefix=arguments.prefix,
                                       min_overlap_s=arguments.min_overlap)
        for proposal in proposals:
            heading = {"new": f"{proposal.take}  (new)", "join": f"{proposal.take}  (joined by)",
                       "skip": f"left alone: {proposal.reason}"}[proposal.action]
            print(heading)
            for index, recording in enumerate(proposal.recordings):
                mark = "primary" if proposal.action == "new" and index == 0 else recording.role
                print(f"  {mark:<8} {Path(recording.path).name}")
        wanted = [p for p in proposals if p.action != "skip"]
        added = group.apply(wanted)
    print(f"{added} recordings placed in {len(wanted)} takes"
          + ("; nothing was written" if arguments.dry_run else ""))


def run_match(arguments: argparse.Namespace) -> None:
    from ..takes import match

    matches = match.find(arguments.project)
    for found in matches:
        name = Path(found.recording_path).name
        if found.camera is None:
            print(f"  no camera    {name}")
            continue
        state = "matched" if found.confirmed else f"unconfirmed ({found.verdict.state})"
        print(f"  {state:<12} {name}  with  {Path(found.camera_path).name}  "
              f"{found.alignment.offset_s:+.2f}s  {found.alignment.votes} shared words")
    confirmed = sum(found.confirmed for found in matches)
    if arguments.dry_run:
        print(f"{confirmed} of {len(matches)} recordings matched; nothing was written")
        return
    added, left = match.apply(matches, prefix=arguments.prefix)
    for found, reason in left:
        print(f"  not added    {Path(found.recording_path).name}: {reason}")
    print(f"{added} of {len(matches)} recordings added to their camera's take")


def run_sync(arguments: argparse.Namespace) -> int:
    from ..takes import sync
    from ..takes.check import MISMATCH

    chosen = sync.chosen(arguments.takes, arguments.project)
    by_sound = not arguments.no_sound and not arguments.check
    if by_sound and not sync.can_listen():
        print("comparing by sound needs numpy, which is not installed; using words and clocks only")
    wrong = unplaced = 0
    for take in chosen:
        if arguments.check:
            placements = sync.recheck(take)
        else:
            placements = sync.sync(take, by_sound=by_sound, write=not arguments.dry_run).placements
        print(take.name)
        for placement in placements:
            print(f"  {_line(placement)}")
            wrong += placement.verdict is not None and placement.verdict.state == MISMATCH
            unplaced += not placement.placed
    summary = f"{len(chosen)} takes: {wrong} wrong, {unplaced} not placed"
    print(summary + ("; nothing was written" if arguments.dry_run and not arguments.check else ""))
    return 1 if wrong else 0


def run_takes(arguments: argparse.Namespace) -> None:
    from .. import takes

    if arguments.take:
        _show(takes.find(arguments.take))
        return
    listed = takes.listing(arguments.project)
    for take in listed:
        print(f"  {take.placed:>3} of {take.members:<3} placed  {take.name}"
              + (f"  ({take.project})" if take.project else ""))
    print(f"{len(listed)} takes")


def run_take(arguments: argparse.Namespace) -> None:
    from .. import db, takes
    from ..takes import TakeError, hand

    name = arguments.name
    try:
        offsets = [(named, float(seconds)) for named, seconds in arguments.offset]
    except ValueError:
        raise TakeError("an offset is a number of seconds: --offset RECORDING SECONDS") from None
    # One unit: a take half mended is worse than one not mended at all.
    with db.transaction(keep=not arguments.dry_run):
        try:
            takes.find(name)
            made = False
        except TakeError:
            if not arguments.primary:
                raise TakeError(f"no take is called {name!r}; to make it, say which recording is its "
                                "primary with --primary") from None
            made = True
        take, _ = hand.open_take(name, arguments.project)
        if made:
            print(f"made take {name}")
            member, _ = hand.put(take, hand.wanted(arguments.primary), primary=True)
            _did("primary", member, take, hand)
        for spec in arguments.add:
            member, happened = hand.put(take, hand.wanted(spec))
            _did(happened, member, take, hand)
        for named, seconds in offsets:
            member = hand.place(take, named, seconds)
            print(f"  {'set by hand':<12} {member.label}  {seconds:+.2f}s")
        if arguments.primary and not made:
            member, happened = hand.put(take, hand.wanted(arguments.primary))
            if happened != "as it was":
                _did(happened, member, take, hand)
            member, changed = hand.lead(take, member)
            print(f"  {'primary':<12} {member.label}"
                  + ("; every offset is now measured from it" if changed else ", as it was"))
        for named in arguments.forget:
            member = hand.forget(take, named)
            print(f"  {'forgotten':<12} the offset of {member.label}; `media sync` places it afresh")
        for named in arguments.remove:
            member = hand.remove(take, named)
            print(f"  {'taken out':<12} {member.label}")
        _show(takes.find(name))
    if arguments.dry_run:
        print("nothing was written")


def _did(happened: str, member: Any, take: Any, hand: Any) -> None:
    also = hand.elsewhere(take, member.asset_id)
    print(f"  {happened:<12} {member.label}  as {member.role}"
          + (f"; it is also in {', '.join(also)}" if also and happened in ("added", "primary") else ""))


def _show(take: Any) -> None:
    from .. import takes
    from ..takes import sync

    print(f"{take.name}" + (f"  ({take.project})" if take.project else ""))
    for placement in sync.verify(takes.members(take.id), {}):
        print(f"  {_line(placement, checked=False)}")


def run_overlap(arguments: argparse.Namespace) -> int:
    from .. import takes
    from ..edit import clock
    from ..takes import hand, overlap

    take = takes.find(arguments.take)
    against = hand.member(take, arguments.against) if arguments.against else None
    only = hand.member(take, arguments.recording) if arguments.recording else None
    compared = overlap.compare(take, against=against, only=only, min_alike=arguments.min_alike,
                               tolerance_s=arguments.tolerance)
    if not compared:
        print(f"{take.name} has one recording: there is nothing to compare it with")
        return 1
    ref = compared[0].ref
    print(f"{take.name}: what each recording says that {ref.label} says too")
    told = 0
    for one in compared:
        other, found = one.other, one.shared
        print(f"{other.label}  ({other.role})")
        print("  stored  " + ("not placed" if one.stored_s is None
                              else f"{one.stored_s:+.2f}s  {other.method or ref.method or 'primary'}"))
        if found.offset_s is None:
            print(f"  shared  nothing to rest an offset on: {found.reason}")
        else:
            told += 1
            print(f"  shared  {found.offset_s:+.2f}s  {len(found.pairs)} of the {found.found} sentences both say "
                  f"agree, to within {found.spread_s:.2f}s"
                  + ("" if one.stored_s is None
                     else f"; {abs(found.offset_s - one.stored_s):.2f}s from the stored offset"))
        if found.pairs:
            print(f"  {_short(ref.label):>12}  {_short(other.label):>12}   implies  alike")
            shown = _spread(found.pairs, arguments.show)
            for pair in shown:
                print(f"  {clock(pair.ref.start_s):>12}  {clock(pair.other.start_s):>12}  {pair.offset_s:+7.2f}s  "
                      f"{pair.alike:5.2f}  {pair.other.text[:64]}")
            if len(shown) < len(found.pairs):
                print(f"  and {len(found.pairs) - len(shown)} more between them")
        if found.offset_s is not None and one.stored_s is None and ref.is_primary:
            print(f"  to store it: media take {shlex.quote(take.name)} --offset {shlex.quote(other.label)} "
                  f"{found.offset_s:+.2f}")
    return 0 if told else 1


def _short(label: str) -> str:
    return Path(label).name[:12]


def _spread(items: Any, most: int) -> list[Any]:
    """At most `most` of `items`, from first to last and evenly between."""
    if most <= 0:
        return []
    if len(items) <= most:
        return list(items)
    if most == 1:
        return [items[0]]
    return [items[round(at * (len(items) - 1) / (most - 1))] for at in range(most)]


def run_pull(arguments: argparse.Namespace) -> int:
    from .. import takes
    from ..edit import clock
    from ..takes import pull

    take = takes.find(arguments.take)
    pulled = pull.pull(take, arguments.at, arguments.seconds, into=arguments.to)
    print(f"{take.name} at {clock(arguments.at)} for {arguments.seconds:g}s")
    made = [one for one in pulled if one.path is not None]
    for one in pulled:
        if one.path is None:
            print(f"  {_short(one.member.label):<12}  {one.reason}")
        else:
            where = f"starts {-one.own_s:.1f}s in" if one.own_s < 0 else f"its {clock(one.own_s)}"
            print(f"  {_short(one.member.label):<12}  {where:<14}  {one.path.name}")
    if not made:
        print(f"no recording of {take.name} gave anything at {clock(arguments.at)}")
        return 1
    print(f"{len(made)} files in {made[0].path.parent}; if the offsets are right, each says the same words")
    return 0


def _line(placement: Any, *, checked: bool = True) -> str:
    from ..takes.check import MISMATCH

    member = placement.member
    if member.is_primary:
        return f"{'primary':<26} {'':>22}  {member.role:<7} {member.label}"
    if not placement.placed:
        return f"{'not placed':<26} {'':>22}  {member.role:<7} {member.label}"
    state = placement.state if checked else "placed"
    how = f"{placement.offset_s:+10.2f}s  {placement.method:<8}"
    verdict = placement.verdict
    if checked and verdict is not None and verdict.state == MISMATCH:
        # The size of the error, never offered as the correction.
        if verdict.beyond_search:
            state += " (far out)"
        elif verdict.residual_s and abs(verdict.residual_s) >= 1:
            state += f" (about {abs(verdict.residual_s):.0f}s out)"
    return f"{state:<26} {how}  {member.role:<7} {member.label}"
