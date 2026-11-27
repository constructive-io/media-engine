"""The `ffmpeg` and `ffprobe` programs: the engine's one decoder and encoder.

Both are run as separate programs and are not part of this package. Everything
that reads or writes picture or sound goes through here, so there is one place
that knows how they are called.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from functools import cache
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

#: H.264 encoders, best first. Hardware where there is some, software otherwise.
H264_ENCODERS = ("h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_vaapi", "libx264", "libopenh264")
FALLBACK_ENCODER = "mpeg4"


class FfmpegError(RuntimeError):
    """ffmpeg or ffprobe is missing, or a run of one failed."""


@dataclass(frozen=True)
class Probe:
    """What a media file is, as far as the engine needs to know."""

    medium: str
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    has_audio: bool = False


def run(program: str, *args: str | Path, binary: bool = False) -> subprocess.CompletedProcess:
    """Run one of the programs to completion and return what it printed."""
    if shutil.which(program) is None:
        raise FfmpegError(f"the {program!r} program was not found; install ffmpeg")
    done = subprocess.run([program, *map(str, args)], capture_output=True, text=not binary)
    if done.returncode:
        stderr = done.stderr if isinstance(done.stderr, str) else done.stderr.decode(errors="replace")
        lines = stderr.strip().splitlines()
        raise FfmpegError(f"{program} failed: {lines[-1] if lines else 'no message'}")
    return done


def probe(path: str | Path) -> Probe | None:
    """Describe a file, or return None when it holds no picture and no sound."""
    try:
        done = run(FFPROBE, "-v", "quiet", "-print_format", "json",
                   "-show_format", "-show_streams", path)
        described = json.loads(done.stdout)
    except (FfmpegError, json.JSONDecodeError) as error:
        if "was not found" in str(error):
            raise
        return None
    streams = described.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video and not audio:
        return None
    duration = _number((described.get("format") or {}).get("duration"))
    if not video:
        return Probe("audio", duration, codec=audio.get("codec_name"), has_audio=True)
    if (video.get("disposition") or {}).get("attached_pic") and audio:
        # Cover art on a sound file is not a video.
        return Probe("audio", duration, codec=audio.get("codec_name"), has_audio=True)
    return Probe(
        "video", duration, video.get("width"), video.get("height"),
        _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        video.get("codec_name"), audio is not None,
    )


def created(path: str | Path) -> datetime | None:
    """When the file says it was recorded, if it says so with a time zone.

    Cameras write this into the container. It is the device's own clock, set
    by hand, and some write the moment the file closed.
    """
    try:
        done = run(FFPROBE, "-v", "quiet", "-print_format", "json",
                   "-show_entries", "format_tags=creation_time", path)
        written = json.loads(done.stdout)["format"]["tags"]["creation_time"]
        found = datetime.fromisoformat(written.replace("Z", "+00:00"))
    except (FfmpegError, KeyError, TypeError, ValueError) as error:
        if "was not found" in str(error):
            raise
        return None
    return found if found.tzinfo else None


@dataclass(frozen=True)
class Start:
    """Where a recording's own clock begins.

    A camera writes a timecode for its first frame, as text. A sound recorder
    writes how long after midnight its first sample was, which is `seconds`.
    """
    timecode: str | None = None
    seconds: Fraction | None = None


def start(path: str | Path) -> Start:
    """The time a file gives its own first frame or sample, where it gives one."""
    try:
        done = run(FFPROBE, "-v", "quiet", "-print_format", "json", "-show_entries",
                   "format_tags=timecode,time_reference:stream_tags=timecode:stream=codec_type,sample_rate", path)
        described = json.loads(done.stdout)
    except (FfmpegError, json.JSONDecodeError) as error:
        if "was not found" in str(error):
            raise
        return Start()
    streams = described.get("streams") or []
    tags = [(described.get("format") or {}).get("tags") or {}] + [each.get("tags") or {} for each in streams]
    timecode = next((each["timecode"] for each in tags if each.get("timecode")), None)
    seconds = None
    samples = next((each["sample_rate"] for each in streams
                    if each.get("codec_type") == "audio" and each.get("sample_rate")), None)
    try:
        if tags[0].get("time_reference") is not None and samples:
            seconds = Fraction(int(tags[0]["time_reference"]), int(samples))
    except (ValueError, ZeroDivisionError):
        seconds = None
    return Start(timecode, seconds)


@cache
def encoders() -> frozenset[str]:
    """The encoders this machine's ffmpeg was built with."""
    listing = run(FFMPEG, "-hide_banner", "-encoders").stdout
    return frozenset(
        parts[1] for line in listing.splitlines()
        if len(parts := line.split()) > 1 and parts[0][:1] in "VAS" and len(parts[0]) == 6
    )


def h264_candidates() -> list[str]:
    """Video encoders to try, in order. A listed encoder can still fail to open."""
    return [name for name in H264_ENCODERS if name in encoders()] + [FALLBACK_ENCODER]


def frame(
    path: str | Path, t_s: float, *, pix_fmt: str = "rgb24",
    size: tuple[int, int] | None = None, still: str | Path | None = None,
) -> bytes:
    """The raw pixels of the frame at `t_s`, optionally also saved as a JPEG.

    Returns no bytes when there is no frame at that time.
    """
    args: list[str | Path] = ["-hide_banner", "-loglevel", "error", "-y",
                              "-ss", f"{max(0.0, t_s):.3f}", "-i", path]
    if still is not None:
        Path(still).parent.mkdir(parents=True, exist_ok=True)
        args += ["-frames:v", "1", "-q:v", "3", "-update", "1", still]
    args += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix_fmt]
    if size:
        args += ["-vf", f"scale={size[0]}:{size[1]}"]
    return run(FFMPEG, *args, "-", binary=True).stdout


def frames(
    path: str | Path, *, fps: float, size: tuple[int, int], pix_fmt: str = "rgb24",
    start_s: float = 0.0, seconds: float | None = None,
) -> Iterator[bytes]:
    """Every frame of a video in order, resampled to a steady `fps`.

    Frame `i` is the picture at `i / fps` seconds, whatever the file's own
    frame timing was. With `start_s` it is the picture that long after there,
    and with `seconds` the frames stop that long after the first.
    """
    if shutil.which(FFMPEG) is None:
        raise FfmpegError(f"the {FFMPEG!r} program was not found; install ffmpeg")
    width, height = size
    length = width * height * _bytes_per_pixel(pix_fmt)
    process = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error",
         *(["-ss", f"{start_s:.3f}"] if start_s > 0 else []), "-i", str(path),
         *(["-t", f"{seconds:.3f}"] if seconds is not None else []), "-an",
         "-vf", f"fps={fps:.6f},scale={width}:{height}",
         "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        while len(chunk := process.stdout.read(length)) == length:
            yield chunk
    finally:
        process.stdout.close()
        process.kill()
        process.wait()


def frame_sizes(path: str | Path, times: Sequence[float]) -> list[tuple[int, int]]:
    """How wide and how tall the picture is at each of some moments in a file.

    The frames themselves are read. What a file says about itself at its
    start is one size, and a file put together from parts can hold another.
    """
    if not times:
        return []
    moments = ",".join(f"{max(0.0, time):.3f}%+#1" for time in times)
    done = run(FFPROBE, "-v", "error", "-select_streams", "v:0", "-read_intervals", moments,
               "-show_entries", "frame=width,height", "-print_format", "json", path)
    try:
        found = json.loads(done.stdout).get("frames") or []
    except json.JSONDecodeError:
        return []
    return [(int(each["width"]), int(each["height"])) for each in found if each.get("width") and each.get("height")]


def samples(path: str | Path, *, rate: int, chunk: int = 1 << 20) -> Iterator[bytes]:
    """The sound of a file as one channel of 16-bit samples, a chunk at a time.

    A file with no sound gives nothing.
    """
    if shutil.which(FFMPEG) is None:
        raise FfmpegError(f"the {FFMPEG!r} program was not found; install ffmpeg")
    process = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
         "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        while piece := process.stdout.read(chunk):
            yield piece
    finally:
        process.stdout.close()
        process.kill()
        process.wait()


def encode(*args: str | Path, video_codecs: Sequence[str] | None = None) -> str | None:
    """Run an encode, trying each video encoder until one works.

    `args` are ffmpeg's arguments with `{vcodec}` wherever the encoder's name
    belongs. Returns the encoder that worked.
    """
    last: FfmpegError | None = None
    for codec in video_codecs or h264_candidates():
        try:
            run(FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                *(str(a).replace("{vcodec}", codec) for a in args))
            return codec
        except FfmpegError as error:
            last = error
    if last:
        raise last
    return None


def _bytes_per_pixel(pix_fmt: str) -> int:
    try:
        return {"rgb24": 3, "bgr24": 3, "gray": 1}[pix_fmt]
    except KeyError:
        raise ValueError(f"unsupported pixel format {pix_fmt!r}") from None


def _number(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _rate(raw: str | None) -> float | None:
    numerator, _, denominator = (raw or "").partition("/")
    top, bottom = _number(numerator), _number(denominator or "1")
    return top / bottom if top and bottom else None
