"""A render's manifest as a timeline a page can follow.

    timeline.build("out/scene.manifest.json", "out/scene.stage")

writes a folder that holds everything a page needs and nothing it does not:

    timeline.json     what fell when
    sound.flac        the mix
    clips/clip_01.mp4 each `[video …]` clip, cut to what is shown, without its sound
    faces/ada.png     a picture for a persona, when there is one

Every path in the timeline is relative to the timeline, so the folder can be
served from anywhere or copied into a site.

    {
      "version": 1,
      "label": "scene",
      "sound": "sound.flac",
      "seconds": 42.5,
      "slides":   [{"t": 0.0, "id": "title", "step": 0}],
      "speakers": [{"t": 0.0, "show": ["faces", "captions"]}],
      "clips":    [{"t": 12.0, "seconds": 4.0, "src": "clips/clip_01.mp4", "sound": "DEMO"}],
      "lines":    [{"t": 0.4, "seconds": 2.1, "line": 1, "speaker": "ADA", "persona": "ada",
                    "track": "dialogue", "text": "…", "name": "Ada", "title": "Engineer",
                    "loudness": [0, 41, 77]}],
      "faces":    {"ada": "faces/ada.png"},
      "loudness": {"rate": 25, "top": 99}
    }

A line's `loudness` is how loud it was, `rate` times a second from where the
line starts, from 0 to `top`. It is what a waveform or a talking face moves to.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from array import array
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .. import ffmpeg
from . import StageError

VERSION = 1
#: Loudness values a second.
RATE = 25
TOP = 99
_SAMPLES = 8000
#: Sound a browser plays as it is. Anything else is written again as one of these.
_PLAYS = (".flac", ".wav", ".mp3", ".m4a", ".ogg", ".opus")
_PICTURES = (".png", ".jpg", ".jpeg", ".webp", ".svg")


def read(manifest: str | Path) -> dict[str, Any]:
    """A render's manifest, from its own path or from the path of the sound it is beside."""
    path = Path(manifest)
    if path.suffix != ".json":
        path = Path(f"{path.with_suffix('')}.manifest.json")
    if not path.is_file():
        raise StageError(f"{path}: no such manifest. `me say` writes one beside the sound it renders")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        report = None
    if not isinstance(report, dict) or "marks" not in report or "output" not in report:
        raise StageError(f"{path} is not the manifest of a render")
    return report


def build(manifest: str | Path | dict[str, Any], folder: str | Path, *, size: tuple[int, int] = (1920, 1080),
          fps: int = 30, faces: str | Path | None = None, more_faces: Mapping[str, str | Path] | None = None,
          tell: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Write the timeline of a render, and all it names, into `folder`. Returns the timeline.

    `size` and `fps` are what the clips are cut to. `faces` is a folder of
    pictures named for personas, such as `ada.png`. `more_faces` are pictures
    by persona, for whoever has none in that folder.
    """
    report = manifest if isinstance(manifest, dict) else read(manifest)
    said = tell or (lambda message: None)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    mix = Path(report["output"])
    if not mix.is_file():
        raise StageError(f"{mix}: the sound this manifest is of is not there. Render it again with `me say`")
    sound = folder / f"sound{mix.suffix.lower() if mix.suffix.lower() in _PLAYS else '.wav'}"
    if sound.suffix == mix.suffix.lower():
        shutil.copyfile(mix, sound)
    else:
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", mix, sound)

    marks = sorted(report.get("marks") or [], key=lambda mark: mark["t"])
    files = {one["line"]: one.get("file") for one in report.get("lines") or []}
    lines, loud = [], []
    for mark in marks:
        if mark["kind"] != "line":
            continue
        line = {key: mark[key] for key in ("t", "seconds", "line", "speaker", "persona", "track", "text",
                                           "name", "title") if key in mark}
        heard = _envelope(files.get(mark["line"]), float(mark["seconds"]))
        if heard is None:
            said(f"  line {mark['line']} is not on disk, so nothing moves to its voice")
        loud.append(heard or [])
        lines.append(line)
    for line, heard in zip(lines, _scaled(loud)):
        line["loudness"] = heard

    clips = _clips(marks, folder, size, fps, said)
    timeline = {
        "version": VERSION,
        "label": report.get("label") or mix.stem,
        "sound": sound.name,
        "seconds": float(report["seconds"]),
        "slides": [{"t": mark["t"], "id": mark["id"], "step": int(mark.get("step") or 0)}
                   for mark in marks if mark["kind"] == "slide" and mark.get("id")],
        "speakers": [{"t": mark["t"], "show": list(mark.get("show") or [])}
                     for mark in marks if mark["kind"] == "speakers"],
        "clips": clips,
        "lines": lines,
        "faces": _faces(faces, more_faces or {}, {line["persona"] for line in lines}, folder),
        "loudness": {"rate": RATE, "top": TOP},
    }
    (folder / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False) + "\n", encoding="utf-8")
    return timeline


def _clips(marks: list[dict[str, Any]], folder: Path, size: tuple[int, int], fps: int,
           said: Callable[[str], None]) -> list[dict[str, Any]]:
    width, height = size
    made: dict[tuple[str, float, float], str] = {}
    clips = []
    for mark in marks:
        if mark["kind"] != "video":
            continue
        if not mark.get("file") or not Path(mark["file"]).is_file():
            said(f"  {mark.get('sound')} has no file here, so the screen is left as it is while it plays")
            continue
        cut = (mark["file"], float(mark.get("start") or 0.0), float(mark["seconds"]))
        if cut not in made:
            made[cut] = f"clips/clip_{len(made) + 1:02d}.mp4"
            target = folder / made[cut]
            target.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.encode(
                "-ss", f"{cut[1]:.3f}", "-t", f"{cut[2]:.3f}", "-i", cut[0], "-an",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:-1:-1,fps={fps},format=yuv420p",
                "-c:v", "{vcodec}", "-b:v", str(bitrate(size, fps)), "-movflags", "+faststart", target,
            )
        clips.append({"t": mark["t"], "seconds": cut[2], "src": made[cut], "sound": mark.get("sound")})
    return clips


def bitrate(size: tuple[int, int], fps: int) -> int:
    """Bits a second that keep text sharp at this size: about 8 Mbit/s for 1080p30."""
    return max(1_000_000, int(size[0] * size[1] * fps * 0.13))


#: The least a film is written at: below it no encoder keeps a picture together.
LEAST_BITS = 50_000


def bits(written: str | int) -> int:
    """Bits a second as somebody writes them: `2M`, `800k`, or the number itself."""
    text = str(written).strip().lower()
    times = {"k": 1_000, "m": 1_000_000}.get(text[-1:], 1)
    number = text[:-1] if times > 1 else text
    try:
        rate = int(float(number) * times)
    except (ValueError, OverflowError):
        rate = 0
    if rate < LEAST_BITS:
        raise StageError(f"{written}: a bitrate is bits a second, such as 2M or 800k, and at least "
                         f"{LEAST_BITS // 1000}k")
    return rate


def _faces(faces: str | Path | None, more: Mapping[str, str | Path], personas: set[str],
           folder: Path) -> dict[str, str]:
    pictures: dict[str, Path] = {}
    if faces is not None and Path(faces).is_dir():
        for picture in sorted(Path(faces).iterdir()):
            if picture.suffix.lower() in _PICTURES:
                pictures.setdefault(picture.stem, picture)
    for persona, picture in more.items():
        if Path(picture).suffix.lower() in _PICTURES and Path(picture).is_file():
            pictures.setdefault(persona, Path(picture))
    found: dict[str, str] = {}
    for persona in sorted(personas & set(pictures)):
        (folder / "faces").mkdir(exist_ok=True)
        kept = f"{persona}{pictures[persona].suffix.lower()}"
        shutil.copyfile(pictures[persona], folder / "faces" / kept)
        found[persona] = f"faces/{kept}"
    return found


def _envelope(file: str | None, seconds: float) -> list[float] | None:
    """How loud a recording is, `RATE` times a second, for its first `seconds`."""
    if not file or not Path(file).is_file():
        return None
    samples = array("h")
    data = b"".join(ffmpeg.samples(file, rate=_SAMPLES))
    samples.frombytes(data[:len(data) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    window = _SAMPLES // RATE
    heard = []
    for begin in range(0, min(len(samples), int(seconds * _SAMPLES)), window):
        piece = samples[begin:begin + window]
        heard.append(math.sqrt(sum(value * value for value in piece) / len(piece)))
    return heard


def _scaled(lines: list[list[float]]) -> list[list[int]]:
    """Every line's loudness from 0 to `TOP`, against the loudest the whole recording nearly reaches."""
    values = sorted(value for line in lines for value in line if value > 0)
    if not values:
        return [[] for _ in lines]
    top = values[min(len(values) - 1, int(len(values) * 0.98))]
    return [[min(TOP, round(value / top * TOP)) for value in line] for line in lines]
