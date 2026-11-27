"""A page filmed against a recording, and written as a video.

    report = film.film("out/scene.manifest.json", "out/scene.mp4", page="deck")

The recording is staged, the page is served and opened in a browser with no
window, and the browser is watched until the page says it is done. Then the
pictures are laid on the recording's clock: each is shown from the moment it
was painted until the next one, the clips are put over them where they play,
and the sound goes under it all.

The film is as long as the sound, to the frame.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping
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
