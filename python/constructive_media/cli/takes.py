"""Commands for takes: group, match, sync and takes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def register(commands: Any) -> None:
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
    from ..takes import sync

    if arguments.take:
        take = takes.find(arguments.take)
        print(f"{take.name}" + (f"  ({take.project})" if take.project else ""))
        for placement in sync.verify(takes.members(take.id), {}):
            print(f"  {_line(placement, checked=False)}")
        return
    listed = takes.listing(arguments.project)
    for take in listed:
        print(f"  {take.placed:>3} of {take.members:<3} placed  {take.name}"
              + (f"  ({take.project})" if take.project else ""))
    print(f"{len(listed)} takes")


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
