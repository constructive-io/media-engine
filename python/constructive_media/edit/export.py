"""A cut handed to an editor, pointing at the recordings themselves.

Everything before this works from small copies, because nobody needs every
pixel to decide whether a shot is sharp. A finished film does need them. So
what is written here names the original files where they already are, and
copies nothing: an editing program opens the cut and has the full recordings
under it at once.

Two files say the same thing. FCPXML carries all of it: picture from one
recording with sound from another under it, names, both kept in step. A
CMX3600 list carries much less and is read by nearly everything, which is
what is wanted on the day the first one will not open.

Three things decide whether a cut opens as it was made:

- **Frame rate.** A camera that says 29.97 means 30000 frames every 1001
  seconds. Both files count in that exact fraction, clip by clip from the
  start of the cut, so they agree with each other and nothing builds up. A
  cut whose recordings were shot at different rates is exported at the rate
  most of it was shot at, and says that it is mixed.
- **The recording's own clock.** Many cameras number their first frame with
  the time of day and not with nought, and a sound recorder stores how long
  after midnight it started. An editing program finds a moment in a file by
  that clock. It is read from each file and every time is given on it.
- **Addresses.** A file is named by a URI, in which a space is not allowed,
  and a clip is named by what somebody said, which may have a quotation mark
  in it. Both are escaped.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from .. import db, ffmpeg
from . import EditError, clock, render, timelines
from .timelines import Clip

#: The reel every event of the list is on: none, the file being named in a comment.
REEL = "AX"
#: The longest a clip's name is allowed to be.
NAMED = 70


def counted(per_s: Fraction) -> int:
    """How many frames a timecode counts to the second: 30 at 29.97."""
    return max(round(per_s), 1)


def dropping(timecode: str | None, per_s: Fraction) -> bool:
    """Whether a timecode skips numbers to keep up with the clock.

    At 29.97 a count of thirty to the second falls behind by a little over a
    frame a minute. Drop-frame timecode leaves out two numbers at the start of
    every minute but each tenth, and is written with a semicolon.
    """
    return bool(timecode) and ";" in timecode and per_s.denominator == 1001 and counted(per_s) % 30 == 0


def frames_of(timecode: str, per_s: Fraction) -> int:
    """How many frames after `00:00:00:00` a timecode is."""
    parts = re.split(r"[:;.]", timecode.strip())
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        raise EditError(f"{timecode!r} is not a timecode")
    hours, minutes, seconds, frames = map(int, parts)
    each = counted(per_s)
    found = (hours * 3600 + minutes * 60 + seconds) * each + frames
    if dropping(timecode, per_s):
        minutes += hours * 60
        found -= each // 15 * (minutes - minutes // 10)
    return found


def timecode_of(frames: int, per_s: Fraction, drop: bool = False) -> str:
    """The timecode of a frame counted from `00:00:00:00`."""
    each = counted(per_s)
    frames = max(int(frames), 0)
    if drop and per_s.denominator == 1001 and each % 30 == 0:
        left_out = each // 15
        tens, rest = divmod(frames, 600 * each - 9 * left_out)
        frames += 9 * left_out * tens
        if rest > left_out:
            frames += left_out * ((rest - left_out) // (60 * each - left_out))
    else:
        drop = False
    seconds, over = divmod(frames, each)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours % 24:02d}:{minutes:02d}:{seconds:02d}{';' if drop else ':'}{over:02d}"


@dataclass(frozen=True)
class Source:
    """A recording as an editor needs it described."""
    asset_id: str
    file: Path
    medium: str = "video"
    fps: float | None = None
    duration_s: float | None = None
    has_audio: bool = True
    width: int | None = None
    height: int | None = None
    #: What the file numbers its first frame, if it numbers it.
    timecode: str | None = None
    #: How long after midnight a sound recording says it began.
    after_midnight: Fraction | None = None
    there: bool = True

    @property
    def rate(self) -> Fraction | None:
        return render.rate(self.fps) if self.medium == "video" and self.fps else None

    def begins(self, per_s: Fraction) -> Fraction:
        """What the file's own clock reads at its first frame or sample, in seconds."""
        own = self.rate or per_s
        if self.timecode and self.medium == "video":
            try:
                return Fraction(frames_of(self.timecode, own)) / own
            except EditError:
                return Fraction(0)
        if self.after_midnight is not None and self.medium == "audio":
            return Fraction(self.after_midnight)
        return Fraction(0)

    def at(self, at_s: float, per_s: Fraction) -> Fraction:
        """What the file's own clock reads `at_s` seconds into it, to the nearest frame.

        Frames are the file's own where it has them and the cut's where it has
        not, as a sound recording has not.
        """
        own = self.rate or per_s
        return self.begins(per_s) + Fraction(max(round(Fraction(at_s) * own), 0)) / own


@dataclass(frozen=True)
class Event:
    """One clip of a cut, placed in frames from the start of it."""
    number: int
    at: int
    frames: int
    picture: Source
    picture_s: float
    #: The recording that is heard, when it is not simply what is under the picture.
    sound: Source | None = None
    sound_s: float | None = None
    name: str = ""


@dataclass(frozen=True)
class Exported:
    """What an export wrote, and what an editor should be told about it."""
    folder: Path
    fcpxml: Path
    edl: Path
    readme: Path
    clips: int
    sources: int
    duration_s: float
    rate: Fraction
    #: Every frame rate among the recordings. More than one is a mixed cut.
    rates: tuple[Fraction, ...] = ()
    missing: tuple[Path, ...] = ()
    #: Clips too short to be given a frame, by their number in the cut.
    left_out: tuple[int, ...] = ()
    #: Whatever else was put in the folder to go with them: each file, and what it is.
    beside: tuple[tuple[Path, str], ...] = ()

    @property
    def mixed(self) -> bool:
        return len(self.rates) > 1


def laid(clips: Sequence[Clip], shelf: Mapping[str, Source], per_s: Fraction) -> list[Event]:
    """A cut's clips as events, each a whole number of frames, none of the rounding kept.

    Where one clip ends is where the next begins, counted from the start of
    the cut, so the last clip is no further from the truth than the first.
    """
    events: list[Event] = []
    total = 0.0
    for number, clip in enumerate(clips, 1):
        at = round(Fraction(total) * per_s)
        total += max(clip.duration_s, 0.0)
        frames = round(Fraction(total) * per_s) - at
        if frames <= 0:
            continue
        picture = shelf[str(clip.picture)]
        apart = clip.sound is not None and clip.sound_start_s is not None and not (
            str(clip.sound) == str(clip.picture) and abs(clip.sound_start_s - clip.start_s) < 1e-6)
        name = " ".join((clip.label or picture.file.stem).split())
        events.append(Event(
            number, at, frames, picture, clip.start_s,
            shelf[str(clip.sound)] if apart else None, clip.sound_start_s if apart else None,
            name if len(name) <= NAMED else name[:NAMED - 1].rstrip() + "…",
        ))
    return events


def edl(title: str, events: Sequence[Event], per_s: Fraction) -> str:
    """The cut as a CMX3600 edit decision list.

    Picture and sound from different recordings are two events at the same
    place. Each names its file in a comment, since a reel name has room for
    eight letters and file names do not fit in eight letters.
    """
    lines = [f"TITLE: {' '.join(title.split())}", "FCM: NON-DROP FRAME", ""]
    number = 0

    def event(track: str, source: Source, at_s: float, placed: Event) -> None:
        nonlocal number
        number += 1
        own = source.rate or per_s
        first = round(source.at(at_s, per_s) * own)
        length = max(round(placed.frames * own / per_s), 1)
        drop = dropping(source.timecode, own) and source.medium == "video"
        lines.append(
            f"{number:03d}  {REEL:<8} {track:<5} C        "
            f"{timecode_of(first, own, drop)} {timecode_of(first + length, own, drop)} "
            f"{timecode_of(placed.at, per_s)} {timecode_of(placed.at + placed.frames, per_s)}")
        lines.append(f"* FROM CLIP NAME: {source.file.name}")
        lines.append(f"* SOURCE FILE: {source.file}")

    for placed in events:
        seen = placed.picture
        own_sound = placed.sound is None and seen.has_audio and seen.medium == "video"
        event("AA/V" if own_sound else "V", seen, placed.picture_s if seen.medium == "video" else 0.0, placed)
        if placed.name and placed.name != seen.file.stem:
            lines.append(f"* COMMENT: {placed.name}")
        if placed.sound is not None:
            event("A", placed.sound, placed.sound_s or 0.0, placed)
        lines.append("")
    return "\n".join(lines) + "\n"


def _seconds(value: Fraction) -> str:
    """A time as FCPXML writes one: a whole number or a fraction of seconds."""
    value = Fraction(value)
    return f"{value.numerator}s" if value.denominator == 1 else f"{value.numerator}/{value.denominator}s"


def fcpxml(title: str, events: Sequence[Event], per_s: Fraction, *, library: str = "constructive-media") -> str:
    """The cut as FCPXML 1.9, which Final Cut, Resolve and Premiere read.

    Sound from another recording hangs under its picture as a second clip, and
    the picture's own sound is switched off, so only one of them is heard.
    """
    formats: dict[tuple[Fraction | None, int | None, int | None], str] = {}
    assets: dict[str, tuple[str, Source]] = {}
    resources: list[str] = []

    def numbered() -> str:
        return f"r{len(formats) + len(assets) + 1}"

    def format_of(per: Fraction | None, width: int | None, height: int | None) -> str:
        if (per, width, height) not in formats:
            formats[(per, width, height)] = found = numbered()
            size = f' width="{width}" height="{height}"' if width and height else ""
            timed = (f' frameDuration="{_seconds(1 / per)}"' if per else ' name="FFVideoFormatRateUndefined"')
            resources.append(f"    <format id=\"{found}\"{timed}{size}/>")
        return formats[(per, width, height)]

    def asset_of(source: Source) -> str:
        if source.asset_id in assets:
            return assets[source.asset_id][0]
        pictured = source.medium in ("video", "image")
        shape = format_of(source.rate, source.width, source.height) if pictured else None
        found = numbered()
        assets[source.asset_id] = (found, source)
        own = source.rate or per_s
        described = [f'id="{found}"', f"name={quoteattr(source.file.name)}",
                     f'start="{_seconds(source.begins(per_s))}"']
        if source.medium == "image":
            described.append('duration="0s"')
        elif source.duration_s:
            described.append(f'duration="{_seconds(Fraction(round(Fraction(source.duration_s) * own)) / own)}"')
        if pictured:
            described += ['hasVideo="1"', f'format="{shape}"', 'videoSources="1"']
        if source.has_audio and source.medium != "image":
            described += ['hasAudio="1"', 'audioSources="1"']
        address = quoteattr("file://" + quote(str(source.file)))
        resources.append(f"    <asset {' '.join(described)}>\n"
                         f"      <media-rep kind=\"original-media\" src={address}/>\n"
                         f"    </asset>")
        return found

    # The sequence is the shape of whatever is on screen longest.
    on_screen: dict[tuple[int, int], int] = {}
    for placed in events:
        if placed.picture.width and placed.picture.height:
            shape = (placed.picture.width, placed.picture.height)
            on_screen[shape] = on_screen.get(shape, 0) + placed.frames
    wide, tall = max(on_screen, key=lambda each: (on_screen[each], each)) if on_screen else (1920, 1080)
    sequence = format_of(per_s, wide, tall)

    spine: list[str] = []
    for placed in events:
        seen = placed.picture
        still = seen.medium == "image"
        starts = seen.at(0.0 if still else placed.picture_s, per_s)
        lasts = Fraction(placed.frames) / per_s
        described = [f'ref="{asset_of(seen)}"', f'offset="{_seconds(Fraction(placed.at) / per_s)}"',
                     f"name={quoteattr(placed.name)}", f'start="{_seconds(starts)}"',
                     f'duration="{_seconds(lasts)}"']
        under = ""
        if placed.sound is not None:
            heard = placed.sound
            only = ' srcEnable="audio"' if heard.medium == "video" else ""
            under = (f'\n              <asset-clip ref="{asset_of(heard)}" lane="-1" offset="{_seconds(starts)}" '
                     f"name={quoteattr(heard.file.name)} start=\"{_seconds(heard.at(placed.sound_s or 0.0, per_s))}\" "
                     f'duration="{_seconds(lasts)}"{only} audioRole="dialogue"/>\n            ')
            if seen.has_audio and not still:
                described.append('srcEnable="video"')
        kind = "video" if still else "asset-clip"
        spine.append(f"            <{kind} {' '.join(described)}" + (f">{under}</{kind}>" if under else "/>"))

    total = Fraction(events[-1].at + events[-1].frames) / per_s if events else Fraction(0)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n  <resources>\n'
        + "\n".join(resources)
        + f"\n  </resources>\n  <library>\n    <event name={quoteattr(library)}>\n"
          f"      <project name={quoteattr(' '.join(title.split()))}>\n"
          f'        <sequence format="{sequence}" duration="{_seconds(total)}" tcStart="0s" tcFormat="NDF" '
          'audioLayout="stereo" audioRate="48k">\n          <spine>\n'
        + "\n".join(spine)
        + "\n          </spine>\n        </sequence>\n      </project>\n    </event>\n  </library>\n</fcpxml>\n"
    )


def _per_s(per_s: Fraction) -> str:
    return f"{float(per_s):.5g}"


def manifest(title: str, name: str, events: Sequence[Event], made: Exported) -> str:
    """A page to go with the two files: what they are, what they point at, and what to check."""
    used: dict[str, Source] = {}
    for placed in events:
        for source in (placed.picture, placed.sound):
            if source is not None:
                used.setdefault(source.asset_id, source)
    folders = sorted({str(source.file.parent) for source in used.values()})
    timed = sum(1 for source in used.values() if source.timecode or source.after_midnight is not None)
    lines = [
        f"# {' '.join(title.split())}",
        "",
        f"{len(events)} clips, {clock(made.duration_s)}, from {len(used)} recordings, "
        f"at {_per_s(made.rate)} frames a second.",
        "",
        "## Opening it",
        "",
        f"- `{name}.fcpxml` is the whole cut: picture, the sound that goes under it, and the names. "
        "Final Cut, Resolve and Premiere import it as a timeline.",
        f"- `{name}.edl` is the same cut as a CMX3600 list, for anything that will not read the first. "
        "Picture and sound from different recordings are separate events in it.",
        "",
        "## Frame rate",
        "",
    ]
    if made.mixed:
        lines += [
            f"**The recordings were shot at {', '.join(_per_s(each) for each in made.rates)} frames a second.** "
            f"The cut is written at {_per_s(made.rate)}, the rate most of it was shot at. Set the timeline to "
            "that before importing, and check the last clip against its sound: a rate taken wrongly shows there "
            "first.",
        ]
    else:
        lines += [f"Every recording is {_per_s(made.rate)} frames a second, and so is the cut."]
    lines += [
        "",
        "## What it points at",
        "",
        "Nothing here is a copy. Both files name the recordings where they are kept:",
        "",
        *[f"- `{folder}`" for folder in folders],
        "",
    ]
    if made.missing:
        lines += [f"**{len(made.missing)} of them were not there when this was written,** and will open "
                  "as missing until they are:", "", *[f"- `{file}`" for file in made.missing], ""]
    else:
        lines += [f"All {len(used)} were there when this was written.", ""]
    lines += [
        f"{timed} of the {len(used)} carry a clock of their own, and their times are given on it. "
        "The rest are timed from their first frame.",
        "",
    ]
    if made.left_out:
        lines += ["Too short for a single frame, and left out: clip "
                  + ", ".join(str(number) for number in made.left_out) + ".", ""]
    if made.beside:
        lines += ["## Also in this folder", "",
                  *[f"- `{file.name}` is {what}." for file, what in made.beside],
                  "",
                  "These are for watching. The two files above do not use them.", ""]
    lines += ["## The clips", "", "| | at | picture | from | sound | from | |", "|---|---|---|---|---|---|---|"]
    for placed in events:
        heard, heard_s = placed.sound or placed.picture, placed.sound_s if placed.sound else placed.picture_s
        silent = placed.sound is None and (not placed.picture.has_audio or placed.picture.medium == "image")
        said = "" if placed.name == placed.picture.file.stem else placed.name.replace("|", "/")
        lines.append(
            f"| {placed.number} | {clock(float(Fraction(placed.at) / made.rate))} "
            f"| {placed.picture.file.name} | {clock(placed.picture_s)} "
            + ("| | " if silent else f"| {heard.file.name} | {clock(heard_s or 0.0)} ") + f"| {said} |")
    return "\n".join(lines) + "\n"


def sources(clips: Sequence[Clip]) -> dict[str, Source]:
    """The recordings a cut's clips come from, each with the clock its file carries."""
    wanted = sorted({str(asset) for clip in clips for asset in (clip.picture, clip.sound) if asset is not None})
    cur = db.cursor()
    cur.execute(
        """SELECT id, path, medium, fps, duration_s, has_audio, width, height
             FROM media.assets WHERE id = ANY(%s::uuid[])""",
        (wanted,),
    )
    found = {}
    for asset_id, path, medium, fps, duration, has_audio, width, height in cur.fetchall():
        file = Path(path)
        there = file.is_file()
        begins = ffmpeg.start(file) if there and medium in ("video", "audio") else ffmpeg.Start()
        found[str(asset_id)] = Source(
            str(asset_id), file, medium, float(fps) if fps is not None else None,
            float(duration) if duration is not None else None, bool(has_audio) or medium == "audio",
            width, height, begins.timecode if medium == "video" else None,
            begins.seconds if medium == "audio" else None, there,
        )
    return found


def export(
    name: str, folder: str | Path, *, fps: float | None = None, title: str | None = None,
    beside: Sequence[tuple[Path, str]] = (),
) -> Exported:
    """Write a cut into `folder` as FCPXML, as an edit decision list, and a page about both.

    The recordings are named where they are and not copied. The cut is written
    at the rate most of it was shot at unless `fps` gives another, and called
    what it is called unless `title` gives another name. `beside` is whatever
    else has been put in the folder, each file with what it is, for the page
    to mention.
    """
    cut = timelines.load(name)
    if not cut.clips:
        raise EditError(f"{name!r} has no clips to export")
    shelf = sources(cut.clips)
    for index, clip in enumerate(cut.clips, 1):
        if shelf[str(clip.picture)].medium == "audio":
            raise EditError(f"clip {index} of {name!r} takes its picture from "
                            f"{shelf[str(clip.picture)].file.name}, which has none")
    per_s = render.rate(fps) if fps else render.usual(cut.clips, shelf)
    events = laid(cut.clips, shelf, per_s)
    if not events:
        raise EditError(f"{name!r} is shorter than one frame")
    used: dict[str, Source] = {}
    for placed in events:
        for source in (placed.picture, placed.sound):
            if source is not None:
                used.setdefault(source.asset_id, source)
    placed_numbers = {placed.number for placed in events}
    title = " ".join((title or "").split()) or cut.title or name
    target = Path(folder).expanduser().resolve()
    made = Exported(
        target, target / f"{name}.fcpxml", target / f"{name}.edl", target / "README.md",
        len(events), len(used), float(Fraction(events[-1].at + events[-1].frames) / per_s), per_s,
        tuple(sorted({source.rate for source in used.values() if source.rate})),
        tuple(sorted(source.file for source in used.values() if not source.there)),
        tuple(number for number in range(1, len(cut.clips) + 1) if number not in placed_numbers),
        tuple((Path(file), what) for file, what in beside),
    )
    target.mkdir(parents=True, exist_ok=True)
    made.fcpxml.write_text(fcpxml(title, events, per_s), encoding="utf-8")
    made.edl.write_text(edl(title, events, per_s), encoding="utf-8")
    made.readme.write_text(manifest(title, name, events, made), encoding="utf-8")
    return made
