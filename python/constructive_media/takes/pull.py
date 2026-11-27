"""The same moment of a take, cut from every recording of it, to listen to.

This is the plainest test of a sync there is. If the offsets are right, each
file says the same words, starting at the same instant; if one is wrong, that
file is somewhere else in the conversation, and it takes a few seconds of
listening to hear it.

A moment is given in take time. Each recording is cut at its own time for that
moment, which is take time less its offset, and every file covers the same
stretch: a recording that started part of the way through is given silence up
to where it starts, so that two files played together are in step or are not.

A recording that was not rolling then gives no file, and neither does one
nothing has placed, since where to cut it is exactly what is not known.

Each file is recorded with the offset it was cut under. Offsets get corrected,
and a snippet cut before the correction says something that is no longer
true; with the numbers beside it, `media lineage` can tell which those are.

The files are sound only, one channel, uncompressed. They are something to
listen to and throw away, and are kept in the work folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import db, derive, ffmpeg
from ..settings import Settings
from . import Member, Take, TakeError, members

#: Seconds cut when no length is asked for: long enough for a sentence or two.
SECONDS = 12.0
#: What `media lineage` says made the files.
TOOL = "media pull"


@dataclass(frozen=True)
class Pulled:
    """One recording's part of a moment: the file, or why there is none."""

    member: Member
    path: Path | None = None
    #: Where in the recording's own time the stretch begins. Below nothing when it began before the recording did.
    own_s: float | None = None
    reason: str = ""


def folder(settings: Settings | None = None) -> Path:
    """Where pulled moments are kept."""
    settings = settings or Settings.from_env()
    return settings.work_dir / "pulls"


def _slug(text: str) -> str:
    # A label nobody gave is the recording's path: its file name, without the ending, is what is meant.
    text = Path(text).stem if "/" in text else text
    return re.sub(r"[^A-Za-z0-9_]+", "-", text).strip("-") or "recording"


def _cut(source: str, start_s: float, length_s: float, lead_s: float, target: Path) -> None:
    heard = "aresample=48000,aformat=channel_layouts=mono"
    if lead_s > 0:
        heard += f",adelay={round(lead_s * 1000)}"
    ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{start_s:.3f}", "-t", f"{length_s:.3f}", "-i", source,
               "-vn", "-af", heard, "-c:a", "pcm_s16le", target)


def pull(take: Take, at_s: float, seconds: float = SECONDS, *, into: str | Path | None = None,
         settings: Settings | None = None) -> list[Pulled]:
    """Cut `seconds` of take time from `at_s` out of every recording of a take."""
    if seconds <= 0:
        raise TakeError("a moment has to have some length: give --seconds more than nothing")
    target = (Path(into) if into else folder(settings)).resolve()
    inside = members(take.id)
    names: set[str] = set()
    pulled = []
    for member in inside:
        own = at_s - member.offset_s
        if not member.has_audio:
            pulled.append(Pulled(member, reason="has no sound"))
            continue
        if not member.placed:
            pulled.append(Pulled(member, reason="not placed: run `media sync`, or set its offset with `media take`"))
            continue
        if own + seconds <= 0 or (member.duration_s is not None and own >= member.duration_s):
            pulled.append(Pulled(member, own_s=own, reason="not rolling then"))
            continue
        if not Path(member.source).is_file():
            pulled.append(Pulled(member, own_s=own, reason="its file is not there"))
            continue
        name = _slug(member.label)
        if name in names:
            name = f"{name}-{str(member.asset_id)[:6]}"
        names.add(name)
        path = target / f"{_slug(take.name)}-{at_s:.2f}s-{name}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        start, lead = max(own, 0.0), max(-own, 0.0)
        _cut(member.source, start, seconds - lead, lead, path)
        made = ffmpeg.probe(path)
        if made is None or not made.duration_s:
            # A recording whose length is not known, or is wrong, is only found out here.
            path.unlink(missing_ok=True)
            pulled.append(Pulled(member, own_s=own, reason="nothing to hear there"))
            continue
        with db.transaction():
            derive.record(
                path, "snippet", TOOL,
                [derive.Source(member.asset_id, "sound", start, start + seconds - lead)],
                params={"take": take.name, "take_time_s": at_s, "seconds": seconds, "label": member.label,
                        "offset_s": member.offset_s, "offset_method": member.method or "primary"},
                start_s=0.0, end_s=seconds, project=take.project,
            )
        pulled.append(Pulled(member, path, own))
    return pulled
