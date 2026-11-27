"""A page filmed against a recording, and written as a video.

    report = film.film("out/scene.manifest.json", "out/scene.mp4", page="deck")

The recording is staged, the page is served and opened in a browser with no
window, and the browser is watched until the page says it is done. Then the
pictures are laid on the recording's clock: each is shown from the moment it
was painted until the next one, the clips are put over them where they play,
and the sound goes under it all.

The film is as long as the sound, to the frame.

    report = film.stills("out/scene.manifest.json", "out/stills", page="deck", at=["land:1+2", "48.5"])

Filming takes as long as the recording. A still does not: the page is put at
a moment and a picture is taken of it there, which is how a page that moves
is looked at while it is being made.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .. import browser as browser_slot
from .. import ffmpeg
from ..browser import Browser, Filmed
from ..settings import Settings
from . import StageError, serve, timeline

SIZE = (1920, 1080)
FPS = 30


def film(manifest: str | Path, target: str | Path, *, page: str | Path | None = None, url: str | None = None,
         size: tuple[int, int] = SIZE, fps: int = FPS, faces: str | Path | None = None,
         more_faces: Mapping[str, str | Path] | None = None, keep: str | Path | None = None,
         browser: Browser | None = None, settings: Settings | None = None,
         bitrate: str | int | None = None,
         tell: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Film the page that follows a render, and write it to `target`.

    `page` is a folder with an `index.html`, or an HTML file; without one the
    built-in page is filmed. `url` is a page served somewhere else that keeps
    the stage's contract. `faces` is a folder of pictures named for personas;
    without it a `faces` folder in the page's own is used when there is one,
    and `more_faces` are pictures by persona for whoever has none there.
    `keep` is a folder to leave the timeline, the frames and the clips in;
    otherwise they are thrown away. `bitrate` is the bits a second the video
    is written at, as `2M` or a number; without it, enough to keep small text
    sharp at this size.
    """
    rate = timeline.bitrate(size, fps) if bitrate is None else timeline.bits(bitrate)
    if size[0] % 2 or size[1] % 2 or min(size) < 16:
        raise StageError(f"{size[0]}x{size[1]}: a film's width and height are even numbers")
    if page is not None and url is not None:
        raise StageError("a film is of one page: give a folder or an address, not both")
    report = timeline.read(manifest)
    target = Path(target)
    said = tell or (lambda message: None)
    if faces is None and page is not None:
        beside = (Path(page) if Path(page).is_dir() else Path(page).parent) / "faces"
        faces = beside if beside.is_dir() else None
    scratch = Path(keep) if keep is not None else Path(tempfile.mkdtemp(prefix="stage-"))
    try:
        staged = scratch / "stage"
        made = timeline.build(report, staged, size=size, fps=fps, faces=faces, more_faces=more_faces, tell=tell)
        watcher = browser or browser_slot.browser(settings=settings)
        with serve.Stage(staged, page=page) as stage:
            said(f"  filming {made['seconds']:.1f} seconds, as they pass")
            frames = scratch / "frames"
            shutil.rmtree(frames, ignore_errors=True)
            filmed = watcher.film(stage.url(url, capture=True, size=size), frames, size=size,
                                  seconds=made["seconds"], tell=tell)
        for note in filmed.notes:
            said(f"  the page raised: {note}")
        encoder, shown = assemble(filmed, made, staged, target, size=size, fps=fps, bitrate=rate)
    finally:
        if keep is None:
            shutil.rmtree(scratch, ignore_errors=True)
    return {
        "output": str(target.resolve()), "seconds": made["seconds"], "size": list(size), "fps": fps,
        "frames": shown, "clips": len(made["clips"]), "slides": len(made["slides"]), "lines": len(made["lines"]),
        "encoder": encoder, "bitrate": rate, "bytes": target.stat().st_size, "notes": filmed.notes,
        "page": url or str(page or "built in"),
    }


#: How long before a beat ends its picture is taken, when no moments are asked for.
BEFORE_S = 0.05
_MOMENT = re.compile(r"(?P<slide>[^:+\s]+?)(?::(?P<step>\d+))?(?P<after>\+\d+(?:\.\d+)?)?")


def moments(made: Mapping[str, Any], asked: Sequence[str | float] | None = None) -> list[tuple[float, str]]:
    """The moments to take a picture at, each with the beat it falls in: seconds, and `slide-step`.

    A moment is asked for as seconds into the recording (`48.5`), as a mark
    of the script (`land`, `land:1`), or as so long after a mark (`land:1+2`).
    With none asked for there is one for every beat, as it is left: just
    before the next `[slide …]`, and at the end.
    """
    seconds = float(made["seconds"])
    slides = sorted(made.get("slides") or [], key=lambda mark: float(mark["t"]))

    def beat(at: float) -> str:
        on = [mark for mark in slides if float(mark["t"]) <= at + 1e-6]
        return f"{on[-1]['id']}-{on[-1]['step']}" if on else "start"

    if not asked:
        ends = [float(mark["t"]) for mark in slides[1:]] + [seconds]
        times = sorted({round(max(0.0, end - BEFORE_S), 3) for end in ends})
        return [(at, beat(at)) for at in times]
    found: list[tuple[float, str]] = []
    for one in asked:
        written = str(one).strip()
        try:
            at = float(written)
        except ValueError:
            match = _MOMENT.fullmatch(written)
            step = int(match["step"] or 0) if match else 0
            marks = [mark for mark in slides if match and mark["id"] == match["slide"] and int(mark["step"]) >= step]
            if not marks:
                names = ", ".join(dict.fromkeys(str(mark["id"]) for mark in slides)) or "none"
                raise StageError(f"`{written}` is not a moment of this recording: give seconds, or a slide of the "
                                 f"script as `NAME`, `NAME:STEP` or `NAME:STEP+SECONDS`. Its slides: {names}") from None
            at = float(marks[0]["t"]) + float(match["after"] or 0)
        if not 0 <= at <= seconds:
            raise StageError(f"`{written}` is {at:.2f} seconds in, and the recording is {seconds:.2f} seconds long")
        found.append((at, beat(at)))
    return found


def stills(manifest: str | Path, folder: str | Path, *, at: Sequence[str | float] | None = None,
           page: str | Path | None = None, url: str | None = None, size: tuple[int, int] = SIZE,
           faces: str | Path | None = None, more_faces: Mapping[str, str | Path] | None = None,
           sheets: bool = False, browser: Browser | None = None, settings: Settings | None = None,
           tell: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Write a picture of the page at each of the moments `at` into `folder`, without filming it.

    The page, the faces and the size are as `film` takes them, and `at` is as
    `moments` reads it. A picture is named for its place in the order asked,
    its moment and its beat: `003_034.31s_land-1.png`. What is drawn from the
    script's clock is exactly as a film has it. What arrives by `data-enter`
    is as far along as it would be; anything else a page leaves to the
    browser to move is shown where it ends up.

    The folder holds the pictures of the last asking: the ones an earlier
    asking wrote there are removed, and nothing else in it is touched. With
    `sheets` the pictures are also laid four to a sheet, at half their size,
    as `sheet_01.jpg` and on, for looking at many at once.
    """
    if page is not None and url is not None:
        raise StageError("a picture is of one page: give a folder or an address, not both")
    report = timeline.read(manifest)
    folder = Path(folder)
    if faces is None and page is not None:
        beside = (Path(page) if Path(page).is_dir() else Path(page).parent) / "faces"
        faces = beside if beside.is_dir() else None
    scratch = Path(tempfile.mkdtemp(prefix="stage-"))
    try:
        made = timeline.build(report, scratch / "stage", size=size, fps=FPS, faces=faces, more_faces=more_faces)
        asked = moments(made, at)
        wanted = [(when, folder / f"{number:03d}_{when:06.2f}s_{_named(beat)}.png")
                  for number, (when, beat) in enumerate(asked, start=1)]
        watcher = browser or browser_slot.browser(settings=settings)
        if folder.is_dir():
            for earlier in folder.iterdir():
                if _OURS.fullmatch(earlier.name) and earlier.is_file():
                    earlier.unlink()
        with serve.Stage(scratch / "stage", page=page) as stage:
            notes = watcher.stills(stage.url(url, still=True, size=size), wanted, size=size, tell=tell)
        laid = sheet([file for _, file in wanted], folder, size=size) if sheets else []
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "folder": str(folder.resolve()), "seconds": made["seconds"], "size": list(size), "notes": notes,
        "sheets": [str(file.resolve()) for file in laid],
        "stills": [{"t": when, "beat": beat, "file": str(file.resolve())}
                   for (when, beat), (_, file) in zip(asked, wanted)],
        "page": url or str(page or "built in"),
    }


#: How many pictures go on a sheet, across and down.
SHEET = (2, 2)
_OURS = re.compile(r"\d{3}_\d+\.\d\ds_[A-Za-z0-9._-]+\.png|sheet_\d\d\.jpg")


def sheet(pictures: Sequence[str | Path], folder: str | Path, *, size: tuple[int, int] = SIZE) -> list[Path]:
    """Lay `pictures` four to a sheet, in order, each at half of `size`: `sheet_01.jpg` and on, in `folder`."""
    across, down = SHEET
    each = across * down
    width, height = size[0] // across // 2 * 2, size[1] // down // 2 * 2
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    laid: list[Path] = []
    for number, first in enumerate(range(0, len(pictures), each), start=1):
        group = pictures[first:first + each]
        inputs = [part for picture in group for part in ("-i", picture)]
        sized = "".join(f"[{place}:v]scale={width}:{height},setsar=1[p{place}];" for place in range(len(group)))
        joined = "".join(f"[p{place}]" for place in range(len(group)))
        target = folder / f"sheet_{number:02d}.jpg"
        ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex",
                   f"{sized}{joined}concat=n={len(group)}:v=1:a=0,tile={across}x{down}", "-frames:v", "1",
                   "-q:v", "3", target)
        laid.append(target)
    return laid


def _named(beat: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", beat).strip("-") or "beat"


def assemble(filmed: Filmed, made: dict[str, Any], staged: str | Path, target: str | Path, *,
             size: tuple[int, int] = SIZE, fps: int = FPS, bitrate: int | None = None) -> tuple[str | None, int]:
    """Lay filmed frames on a timeline's clock and write the video. The encoder used, and how many frames went in."""
    staged, target = Path(staged), Path(target)
    seconds = float(made["seconds"])
    placed = sorted(((frame.at - filmed.start, frame.file) for frame in filmed.frames), key=lambda one: one[0])
    # The last picture painted before the clock began is what the film opens on.
    before = [one for one in placed if one[0] <= 0]
    shown = ([(0.0, before[-1][1])] if before else []) + [one for one in placed if 0 < one[0] < seconds]
    if not shown:
        raise StageError("the browser painted nothing while the page ran")
    shown[0] = (0.0, shown[0][1])
    listing = target.parent / f".{target.stem}.frames.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for (at, file), (after, _) in zip(shown, [*shown[1:], (seconds, None)]):
        rows += [f"file '{_quoted(file)}'", f"duration {max(after - at, 0.001):.6f}"]
    rows.append(f"file '{_quoted(shown[-1][1])}'")  # the last is named twice, or its duration is not kept
    listing.write_text("\n".join(rows) + "\n", encoding="utf-8")

    width, height = size
    inputs: list[str | Path] = ["-f", "concat", "-safe", "0", "-i", listing, "-i", staged / made["sound"]]
    graph = [f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
             f"pad={width}:{height}:-1:-1,format=yuv420p[v0]"]
    for number, clip in enumerate(made["clips"]):
        inputs += ["-i", staged / clip["src"]]
        at, length = float(clip["t"]), float(clip["seconds"])
        graph.append(f"[{number + 2}:v]setpts=PTS-STARTPTS+{at:.3f}/TB,format=yuv420p[c{number}]")
        graph.append(f"[v{number}][c{number}]overlay=eof_action=pass:"
                     f"enable='between(t,{at:.3f},{at + length:.3f})'[v{number + 1}]")
    try:
        encoder = ffmpeg.encode(
            *inputs, "-filter_complex", ";".join(graph), "-map", f"[v{len(made['clips'])}]", "-map", "1:a",
            "-c:v", "{vcodec}", "-b:v", str(bitrate or timeline.bitrate(size, fps)), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{seconds:.3f}", "-movflags", "+faststart", target,
        )
    finally:
        listing.unlink(missing_ok=True)
    return encoder, len(shown)


def _quoted(path: Path) -> str:
    return str(Path(path).resolve()).replace("'", "'\\''")
