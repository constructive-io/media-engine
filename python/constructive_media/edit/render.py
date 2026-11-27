"""A cut made into a file that can be watched.

A cut is a list of times, and rendering it means reading those times out of
the recordings they belong to. It is done from the small working copies made
at ingest unless the originals are asked for: a render answers "is this the
right material in the right order", and half the resolution answers that as
well as all of it.

Three things can go wrong quietly, and each is ruled out by how the file is
built, not by hoping:

- **Shape.** Every clip is fitted into one canvas, padded or cropped, before
  anything is joined. Clips of different shapes are never left to inherit the
  shape of the first.
- **Sync.** Picture is counted in frames and sound in samples, both from the
  start of the cut, so rounding never builds up: the picture is within half
  a frame of the sound at the first clip and at the last.
- **Joins.** Clips that carry on the same recording's sound, as the two sides
  of a cutaway do, are heard as one unbroken stretch. Sound is only encoded
  once, at the end, so there is nothing to click where one clip meets the
  next.

Loudness is measured first and then set with one gain for each stretch of
sound, which leaves the stretch itself as it was recorded. A limiter over
the finished sound holds the peaks down.

The file is looked at afterwards, at several points along it, and what was
found is stored beside what was asked for.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .. import db, derive, ffmpeg
from ..settings import Settings
from . import EditError, timelines
from .timelines import Clip

#: Each canvas as so many across to so many up.
CANVASES = {"landscape": (16, 9), "vertical": (9, 16), "square": (1, 1)}
#: How tall a render is unless a height is asked for.
HEIGHTS = {"landscape": 540, "vertical": 1080, "square": 720}
FITS = ("pad", "crop")
#: Sound is worked on and written at this many samples a second.
SAMPLES = 48000
#: Bits for each pixel of each frame, which is what a picture this size is worth.
BITS_PER_PIXEL = 0.19
SOUND_BITRATE = 160_000
#: Under this a picture is not worth sending.
LEAST_BITRATE = 150_000
#: The most clips read in one run of ffmpeg. Each is an open file, and a
#: machine allows a program only so many.
MOST = 12
#: Recordings whose sound joins up to within this are one stretch of sound.
JOINED_S = 0.001
#: A person has to be at least this likely to be one to pull a crop towards them.
PERSON = 0.35
MOVIES = (".mp4", ".mov", ".m4v")
#: How far ahead the limiter looks for a peak, in milliseconds.
LOOK_AHEAD_MS = 5
#: How far through a finished file its frames are looked at.
LOOKED_AT = (0.02, 0.2, 0.4, 0.6, 0.8, 0.97)


@dataclass(frozen=True)
class Recording:
    """A file a cut draws on, and what is known about it."""

    asset_id: str
    file: Path
    medium: str = "video"
    duration_s: float | None = None
    fps: float | None = None
    has_audio: bool = True


@dataclass(frozen=True)
class Loudness:
    """How loud a stretch of sound is: over all of it, and at its loudest."""

    integrated: float | None
    peak: float | None = None


@dataclass(frozen=True)
class Piece:
    """What is seen during one clip: so many frames of a file, from a moment in it."""

    file: Path
    start_s: float
    frames: int
    still: bool = False
    #: Where the people are, across and down, each from 0 to 1.
    centre: tuple[float, float] = (0.5, 0.5)


@dataclass(frozen=True)
class Stretch:
    """Sound that runs unbroken under one clip or several."""

    #: The file it is read from, or nothing where there is only silence.
    file: Path | None
    start_s: float
    samples: int
    #: The first clip it runs under, counting from 0, and how many.
    first: int
    count: int
    gain_db: float = 0.0
    #: Whether the sound starts here and stops here, or is cut from a longer stretch.
    opens: bool = True
    closes: bool = True

    @property
    def duration_s(self) -> float:
        return self.samples / SAMPLES


@dataclass(frozen=True)
class Plan:
    """Everything a render will read, worked out before anything is read."""

    width: int
    height: int
    rate: Fraction
    fit: str
    pieces: tuple[Piece, ...]
    stretches: tuple[Stretch, ...]
    #: When each clip starts in the finished file, and after the last, when it ends.
    times: tuple[float, ...]

    @property
    def duration_s(self) -> float:
        return self.times[-1]

    @property
    def frames(self) -> int:
        return sum(piece.frames for piece in self.pieces)


@dataclass(frozen=True)
class Batch:
    """The clips one run of ffmpeg reads, and the sound under them."""

    pieces: tuple[Piece, ...]
    stretches: tuple[Stretch, ...]


@dataclass(frozen=True)
class Rendered:
    """A file that was made, and what was found on looking at it."""

    path: Path
    clips: int
    #: How long the cut is, and how long the file turned out.
    duration_s: float
    measured_s: float | None
    width: int
    height: int
    rate: Fraction
    encoder: str | None
    #: The sizes of the frames looked at. One size, the canvas's, is what is wanted.
    sizes: tuple[tuple[int, int], ...]
    #: How many frames were looked at.
    looked: int
    #: The gain given to each stretch of sound, in decibels.
    gains: tuple[float, ...]

    @property
    def uniform(self) -> bool:
        return self.sizes == ((self.width, self.height),)

    @property
    def adrift_s(self) -> float:
        """How far the file's length is from the cut's."""
        return 0.0 if self.measured_s is None else self.measured_s - self.duration_s


def size(canvas: str, height: int | None = None) -> tuple[int, int]:
    """How wide and how tall a canvas is at a height. Both come out even, as encoders need."""
    if canvas not in CANVASES:
        raise EditError(f"no canvas is called {canvas!r}; there are: {', '.join(CANVASES)}")
    if height is not None and height < 16:
        raise EditError(f"a render {height} high is too small to see")
    across, up = CANVASES[canvas]
    tall = _even(height or HEIGHTS[canvas])
    return _even(tall * across / up), tall


def rate(fps: float | None) -> Fraction:
    """A frame rate as the exact fraction a camera means by it.

    A camera that says 23.976 means 24000 frames every 1001 seconds. Taken as
    24 it is a frame out every forty seconds, which is enough to see by the
    end of a short cut.
    """
    if not fps or fps <= 0:
        return Fraction(30)
    whole = round(fps * 1001 / 1000)
    if abs(fps - whole * 1000 / 1001) < 0.005:
        return Fraction(whole * 1000, 1001)
    if abs(fps - round(fps)) < 0.005:
        return Fraction(round(fps))
    return Fraction(fps).limit_denominator(1001)


def usual(clips: Sequence[Clip], recordings: Mapping[str, Recording]) -> Fraction:
    """The frame rate most of a cut was shot at, by time on screen."""
    seen: dict[Fraction, float] = {}
    for clip in clips:
        recording = recordings.get(str(clip.picture))
        if recording and recording.fps and recording.medium == "video":
            seen[rate(recording.fps)] = seen.get(rate(recording.fps), 0.0) + max(clip.duration_s, 0.0)
    return max(seen, key=lambda each: (seen[each], each)) if seen else Fraction(30)


def bitrate(
    width: int, height: int, per_s: Fraction, *, megabytes: float | None = None,
    duration_s: float | None = None, silent: bool = False,
) -> int:
    """Bits a second for the picture: what its size is worth, or what fits in `megabytes`."""
    if megabytes is None:
        return max(round(width * height * float(per_s) * BITS_PER_PIXEL), LEAST_BITRATE)
    if megabytes <= 0 or not duration_s:
        raise EditError("a file has to be allowed some megabytes")
    # A little is kept back for what a file holds besides picture and sound.
    return max(round(megabytes * 8_000_000 * 0.97 / duration_s) - (0 if silent else SOUND_BITRATE), LEAST_BITRATE)


def gain(heard: Loudness | None, target: float = -16.0, most: float = 20.0) -> float:
    """The decibels that bring a stretch of sound to the target loudness.

    Silence is left alone, and nothing is turned up by more than `most`:
    past that it is the room being turned up, not a voice.
    """
    if heard is None or heard.integrated is None or heard.integrated < -70.0:
        return 0.0
    return round(min(target - heard.integrated, most), 2)


def heard(printed: str) -> Loudness:
    """Read what ffmpeg's loudness measurement printed."""
    start, end = printed.rfind("{"), printed.rfind("}")
    try:
        found = json.loads(printed[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        found = {}

    def number(key: str) -> float | None:
        try:
            value = float(found[key])
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    return Loudness(number("input_i"), number("input_tp"))


def loudness(file: Path, start_s: float, length_s: float, highpass_hz: float = 0.0) -> Loudness:
    """Measure a stretch of a file the way broadcasters do (EBU R 128)."""
    chain = (f"highpass=f={highpass_hz:g}," if highpass_hz else "") + "loudnorm=print_format=json"
    done = ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-nostats", "-loglevel", "info", "-vn",
                      "-ss", f"{start_s:.6f}", "-t", f"{length_s:.6f}", "-i", file,
                      "-af", chain, "-f", "null", "-")
    return heard(done.stderr)


def plan(
    clips: Sequence[Clip], recordings: Mapping[str, Recording], *, width: int, height: int,
    frames_per_s: Fraction, fit: str = "pad", silent: bool = False,
    centres: Mapping[int, tuple[float, float]] | None = None,
) -> Plan:
    """Work out what each clip shows and what is heard under it.

    A clip lasts as long as its sound. Its picture gets the frames between
    where it starts and where it ends in the finished file, both rounded to a
    frame, so a rounding in one clip is taken back in the next.
    """
    if fit not in FITS:
        raise EditError(f"a picture is fitted by one of: {', '.join(FITS)}; not {fit!r}")
    times = [0.0]
    for clip in clips:
        times.append(times[-1] + max(clip.duration_s, 0.0))
    frame_at = [round(time * frames_per_s) for time in times]
    sample_at = [round(time * SAMPLES) for time in times]
    pieces, stretches = [], []
    for index, clip in enumerate(clips):
        seen = recordings[str(clip.picture)]
        pieces.append(Piece(seen.file, clip.start_s, frame_at[index + 1] - frame_at[index], seen.medium == "image",
                            (centres or {}).get(index, (0.5, 0.5))))
        if silent:
            continue
        apart = clip.sound is not None and clip.sound_start_s is not None
        source = recordings[str(clip.sound if apart else clip.picture)]
        start = clip.sound_start_s if apart else clip.start_s
        file = source.file if source.has_audio and source.medium != "image" else None
        last = stretches[-1] if stretches else None
        if last and last.file == file and (
                file is None or abs(last.start_s + times[index] - times[last.first] - start) < JOINED_S):
            stretches[-1] = Stretch(file, last.start_s, sample_at[index + 1] - sample_at[last.first],
                                    last.first, last.count + 1)
        else:
            stretches.append(Stretch(file, start, sample_at[index + 1] - sample_at[index], index, 1))
    return Plan(width, height, frames_per_s, fit, tuple(pieces), tuple(stretches), tuple(times))


def batches(planned: Plan, most: int = MOST) -> list[Batch]:
    """Divide a plan into runs of at most `most` clips.

    A stretch of sound is kept whole where it can be. One that runs under
    more clips than fit is divided at a clip, to the sample, and neither
    part fades where they meet.
    """
    groups = [(stretch.first, stretch.count) for stretch in planned.stretches] or [
        (index, 1) for index in range(len(planned.pieces))]
    spans: list[tuple[int, int]] = []
    first = count = 0
    for start, length in groups:
        if count and count + length > most:
            spans.append((first, count))
            first, count = start, 0
        if not count:
            first = start
        count += length
        while count > most:
            spans.append((first, most))
            first, count = first + most, count - most
    if count:
        spans.append((first, count))
    return [Batch(planned.pieces[first:first + count],
                  tuple(part for stretch in planned.stretches
                        if (part := _part(planned, stretch, first, count)) is not None))
            for first, count in spans]


def _part(planned: Plan, stretch: Stretch, first: int, count: int) -> Stretch | None:
    """The part of a stretch that lies under `count` clips from `first`."""
    start, end = max(first, stretch.first), min(first + count, stretch.first + stretch.count)
    if start >= end:
        return None
    return Stretch(
        stretch.file, stretch.start_s + planned.times[start] - planned.times[stretch.first],
        round(planned.times[end] * SAMPLES) - round(planned.times[start] * SAMPLES),
        start, end - start, stretch.gain_db,
        opens=start == stretch.first, closes=end == stretch.first + stretch.count,
    )


def fitted(planned: Plan, piece: Piece) -> str:
    """The filters that put one picture on the canvas."""
    width, height = planned.width, planned.height
    if planned.fit == "crop":
        across, down = piece.centre
        # Fill the canvas and lose what does not fit, keeping the people in it.
        return (f"crop='min(iw,ih*{width}/{height})':'min(ih,iw*{height}/{width})':"
                f"'clip(iw*{across:.4f}-ow/2,0,iw-ow)':'clip(ih*{down:.4f}-oh/2,0,ih-oh)',"
                f"scale={width}:{height},setsar=1")
    # Show all of it, with bars where the shapes differ. Nothing is stretched.
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")


def graph(planned: Plan, batch: Batch, *, highpass_hz: float = 80.0, fade_s: float = 0.02) -> tuple[list[str], str]:
    """ffmpeg's inputs and filters for one batch: `[v]` is its picture and `[a]` its sound."""
    per_s = planned.rate
    spoken = f"{per_s.numerator}/{per_s.denominator}"
    inputs: list[str] = []
    chains: list[str] = []
    seen: list[str] = []
    for piece in batch.pieces:
        if not piece.frames:
            continue
        number = inputs.count("-i")
        length = f"{float((piece.frames + 2) / per_s):.6f}"
        if piece.still:
            inputs += ["-loop", "1", "-framerate", spoken, "-t", length, "-i", str(piece.file)]
        else:
            inputs += ["-an", "-ss", f"{max(piece.start_s, 0.0):.6f}", "-t", length, "-i", str(piece.file)]
        # A steady frame rate from the first instant, the last frame held if the
        # recording stops short, and then exactly as many frames as were counted.
        chains.append(f"[{number}:v:0]fps={spoken}:start_time=0,tpad=stop=-1:stop_mode=clone,"
                      f"trim=end_frame={piece.frames},{fitted(planned, piece)},format=yuv420p,"
                      f"setpts=PTS-STARTPTS[v{len(seen)}]")
        seen.append(f"[v{len(seen)}]")
    if seen:
        chains.append(f"{''.join(seen)}concat=n={len(seen)}:v=1:a=0[v]")
    sounds: list[str] = []
    for stretch in batch.stretches:
        if not stretch.samples:
            continue
        number = inputs.count("-i")
        length = stretch.duration_s
        if stretch.file is None:
            inputs += ["-f", "lavfi", "-t", f"{length + 0.1:.6f}", "-i", f"anullsrc=r={SAMPLES}:cl=stereo"]
            steps = []
        else:
            inputs += ["-vn", "-ss", f"{max(stretch.start_s, 0.0):.6f}", "-t", f"{length + 0.1:.6f}",
                       "-i", str(stretch.file)]
            steps = [f"highpass=f={highpass_hz:g}"] if highpass_hz else []
            if stretch.gain_db:
                steps.append(f"volume={stretch.gain_db:.2f}dB")
            if fade_s and length > 2 * fade_s:
                if stretch.opens:
                    steps.append(f"afade=t=in:st=0:d={fade_s:g}")
                if stretch.closes:
                    steps.append(f"afade=t=out:st={length - fade_s:.6f}:d={fade_s:g}")
        # Exactly as many samples as were counted, padded with silence if the recording stops short.
        chain = [f"aresample={SAMPLES}", "aformat=sample_fmts=fltp:channel_layouts=stereo", *steps,
                 f"apad=whole_len={stretch.samples}", f"atrim=end_sample={stretch.samples}", "asetpts=PTS-STARTPTS"]
        chains.append(f"[{number}:a:0]{','.join(chain)}[a{len(sounds)}]")
        sounds.append(f"[a{len(sounds)}]")
    if sounds:
        chains.append(f"{''.join(sounds)}concat=n={len(sounds)}:v=0:a=1[a]")
    return inputs, ";".join(chains)


def centres(clips: Sequence[Clip]) -> dict[int, tuple[float, float]]:
    """Where the people are in each clip, for the clips that have any.

    The middle of every person found in the shots a clip runs through,
    weighed by how much of the frame each fills, so that whoever is nearest
    counts most.
    """
    found: dict[int, tuple[float, float]] = {}
    cur = db.cursor()
    for index, clip in enumerate(clips):
        cur.execute(
            """SELECT sum(d.cx * d.w * d.h) / nullif(sum(d.w * d.h), 0),
                      sum(d.cy * d.w * d.h) / nullif(sum(d.w * d.h), 0)
                 FROM media_video.detections d
                 JOIN media_video.frames f ON f.id = d.frame_id
                 JOIN media_video.shots s ON s.id = f.shot_id
                WHERE s.asset_id = %s AND s.start_s < %s AND s.end_s > %s
                  AND d.class_name = 'person' AND d.confidence >= %s""",
            (clip.picture, clip.end_s, clip.start_s, PERSON),
        )
        across, down = cur.fetchone()
        if across is not None and down is not None:
            found[index] = (min(max(float(across), 0.0), 1.0), min(max(float(down), 0.0), 1.0))
    return found


def recordings(clips: Sequence[Clip], *, originals: bool = False) -> dict[str, Recording]:
    """The files a cut's clips are read from: working copies, or the originals."""
    wanted = sorted({str(asset) for clip in clips for asset in (clip.picture, clip.sound) if asset is not None})
    cur = db.cursor()
    cur.execute(
        """SELECT id, path, proxy_path, medium, duration_s, fps, has_audio
             FROM media.assets WHERE id = ANY(%s::uuid[])""",
        (wanted,),
    )
    return {
        str(asset_id): Recording(str(asset_id), Path(path if originals or not proxy else proxy), medium,
                                 float(duration) if duration is not None else None,
                                 float(fps) if fps is not None else None, bool(has_audio) or medium == "audio")
        for asset_id, path, proxy, medium, duration, fps, has_audio in cur.fetchall()
    }


def render(
    name: str, out: str | Path, *, canvas: str | None = None, fit: str | None = None,
    height: int | None = None, fps: float | None = None, silent: bool = False,
    originals: bool = False, lufs: float | None = -16.0, ceiling_db: float = -1.5,
    highpass_hz: float = 80.0, fade_s: float = 0.02, megabytes: float | None = None,
    settings: Settings | None = None, progress: Callable[[int, int], None] | None = None,
    note: Mapping[str, Any] | None = None,
) -> Rendered:
    """Render a cut to `out` and record what it was made from.

    The canvas and the fit are the cut's own unless others are given. With
    `lufs` set to nothing the sound is left as loud as it was recorded.
    `megabytes` asks for a file of about that size, however long the cut is.
    `note` is more to write down about the file, beside how it was made:
    that it is a preview, say, and not the cut as it is meant to be seen.
    """
    cut = timelines.load(name)
    if not cut.clips:
        raise EditError(f"{name!r} has no clips to render")
    canvas, fit = canvas or cut.canvas, fit or cut.fit
    width, tall = size(canvas, height)
    files = recordings(cut.clips, originals=originals)
    for index, clip in enumerate(cut.clips, 1):
        for asset in (clip.picture, clip.sound):
            if asset is None:
                continue
            if (recording := files.get(str(asset))) is None or not recording.file.is_file():
                where = recording.file if recording else asset
                raise EditError(f"clip {index} of {name!r} is cut from {where}, which is not there")
        seen = files[str(clip.picture)]
        if seen.medium == "audio":
            raise EditError(f"clip {index} of {name!r} takes its picture from {seen.file.name}, which has none")
        if seen.duration_s is not None and seen.medium == "video" and clip.start_s >= seen.duration_s:
            raise EditError(f"clip {index} of {name!r} starts after {seen.file.name} has ended")
    per_s = rate(fps) if fps else usual(cut.clips, files)
    planned = plan(cut.clips, files, width=width, height=tall, frames_per_s=per_s, fit=fit, silent=silent,
                   centres=centres(cut.clips) if fit == "crop" else None)
    if not planned.frames:
        raise EditError(f"{name!r} is shorter than one frame")
    if lufs is not None:
        planned = levelled(planned, lufs, highpass_hz)
    bits = bitrate(width, tall, per_s, megabytes=megabytes, duration_s=planned.duration_s, silent=silent)

    target = Path(out).expanduser().resolve()
    work = (settings or Settings.from_env()).work_dir / "renders"
    encoder = build(planned, target, work, bits=bits, ceiling_db=ceiling_db, highpass_hz=highpass_hz,
                    fade_s=fade_s, progress=progress)

    described = ffmpeg.probe(target)
    measured = described.duration_s if described else None
    looked = ffmpeg.frame_sizes(target, [(measured or planned.duration_s) * part for part in LOOKED_AT])
    sizes = tuple(sorted(set(looked)))
    made = Rendered(target, len(cut.clips), planned.duration_s, measured, width, tall, per_s, encoder, sizes,
                    len(looked), tuple(stretch.gain_db for stretch in planned.stretches))
    with db.transaction():
        derive.record(
            target, "render", "media render", _sources(cut.clips, silent), project=cut.project,
            params={"timeline": cut.name, "revision": cut.revision, "canvas": canvas, "fit": fit, "width": width,
                    "height": tall, "fps": str(per_s), "silent": silent, "originals": originals, "lufs": lufs,
                    **(note or {})},
        )
        common = sizes[0] if sizes else (0, 0)
        db.cursor().execute(
            """INSERT INTO media_video.render_geometry (timeline, path, width, height, uniform, samples, canvas)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (cut.name, str(target), common[0], common[1], made.uniform, len(looked), canvas),
        )
    return made


def levelled(planned: Plan, lufs: float = -16.0, highpass_hz: float = 80.0) -> Plan:
    """The same plan with each stretch of sound given the gain that brings it to `lufs`."""
    stretches = []
    for stretch in planned.stretches:
        if stretch.file is not None and stretch.samples:
            measured = loudness(stretch.file, stretch.start_s, stretch.duration_s, highpass_hz)
            stretch = Stretch(stretch.file, stretch.start_s, stretch.samples, stretch.first, stretch.count,
                              gain(measured, lufs), stretch.opens, stretch.closes)
        stretches.append(stretch)
    return Plan(planned.width, planned.height, planned.rate, planned.fit, planned.pieces, tuple(stretches),
                planned.times)


def build(
    planned: Plan, target: Path, work: Path, *, bits: int | None = None, ceiling_db: float = -1.5,
    highpass_hz: float = 80.0, fade_s: float = 0.02, progress: Callable[[int, int], None] | None = None,
) -> str | None:
    """Make the file a plan describes, and say which encoder made its picture.

    The plan is read a batch at a time into picture without sound and sound
    without picture. Only then are the two put together.
    """
    bits = bits or bitrate(planned.width, planned.height, planned.rate)
    target.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="render-", dir=work))
    try:
        parts = batches(planned)
        pictures: list[Path] = []
        sounds: list[Path] = []
        encoder = None
        for number, batch in enumerate(parts):
            if progress:
                progress(number, len(parts))
            inputs, filters = graph(planned, batch, highpass_hz=highpass_hz, fade_s=fade_s)
            outputs: list[str | Path] = []
            if "[v]" in filters:
                pictures.append(folder / f"{number:04d}.mov")
                outputs += ["-map", "[v]", "-c:v", "{vcodec}", "-b:v", str(bits), "-pix_fmt", "yuv420p",
                            pictures[-1]]
            if "[a]" in filters:
                sounds.append(folder / f"{number:04d}.wav")
                outputs += ["-map", "[a]", "-c:a", "pcm_f32le", "-rf64", "auto", sounds[-1]]
            if not outputs:
                continue
            if "[v]" not in filters:
                ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *inputs,
                           "-filter_complex", filters, *outputs)
                continue
            # One encoder for the whole file: whichever the first batch found to work.
            encoder = ffmpeg.encode(*inputs, "-filter_complex", filters, *outputs,
                                    video_codecs=[encoder] if encoder else None)
        if not pictures:
            raise EditError("there is not one frame of picture to render")
        _join(pictures, sounds, target, folder, ceiling_db)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if progress:
        progress(len(parts), len(parts))
    return encoder


def _join(pictures: Sequence[Path], sounds: Sequence[Path], target: Path, folder: Path, ceiling_db: float) -> None:
    """Put the batches end to end: the picture as it is, the sound limited and encoded once."""
    args: list[str | Path] = ["-hide_banner", "-loglevel", "error", "-y"]
    for label, files in (("pictures", pictures), ("sounds", sounds)):
        if files:
            listing = folder / f"{label}.txt"
            listing.write_text("".join("file '{}'\n".format(str(file).replace("'", "'\\''")) for file in files))
            args += ["-f", "concat", "-safe", "0", "-i", listing]
    args += ["-map", "0:v:0", "-c:v", "copy"]
    if sounds:
        limit = min(max(10 ** (ceiling_db / 20), 0.0625), 1.0)
        # A limiter looks ahead by its attack and hands the sound back that much late,
        # which is put right here: as much is added to the end as is taken off the start.
        late = SAMPLES * LOOK_AHEAD_MS // 1000
        args += ["-map", "1:a:0", "-af",
                 f"apad=pad_len={late},alimiter=limit={limit:.4f}:attack={LOOK_AHEAD_MS}:release=50:level=0,"
                 f"atrim=start_sample={late},asetpts=PTS-STARTPTS",
                 "-c:a", "aac", "-b:a", str(SOUND_BITRATE)]
    if target.suffix.lower() in MOVIES:
        args += ["-movflags", "+faststart"]
    ffmpeg.run(ffmpeg.FFMPEG, *args, target)


def _sources(clips: Sequence[Clip], silent: bool) -> list[derive.Source]:
    """Each recording a render drew on, from the earliest it was used to the latest."""
    spans: dict[tuple[str, str], tuple[Any, float, float]] = {}
    for clip in clips:
        apart = clip.sound is not None and clip.sound_start_s is not None
        used = [(clip.picture, "picture", clip.start_s, clip.start_s + clip.duration_s)]
        if not silent:
            used.append((clip.sound, "sound", clip.sound_start_s, clip.sound_start_s + clip.duration_s) if apart
                        else (clip.picture, "sound", clip.start_s, clip.start_s + clip.duration_s))
        for asset, role, start, end in used:
            _, low, high = spans.get((str(asset), role), (asset, start, end))
            spans[(str(asset), role)] = (asset, min(low, start), max(high, end))
    return [derive.Source(asset, role, start, end) for (_, role), (asset, start, end) in spans.items()]


def side_by_side(
    left: tuple[Path, float], right: tuple[Path, float], out: str | Path, *,
    seconds: float = 4.0, height: int = 360,
) -> Path:
    """Two moments playing next to each other, to choose between by eye.

    The first is on the left and the second on the right, with a white line
    between them and nothing else: no colour that the footage might be using.
    """
    target = Path(out).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tall = _even(height)
    side = f"fps=30,scale=-2:{tall},setsar=1,format=yuv420p"
    ffmpeg.encode(
        "-ss", f"{max(left[1], 0.0):.3f}", "-t", f"{seconds:.3f}", "-i", left[0],
        "-ss", f"{max(right[1], 0.0):.3f}", "-t", f"{seconds:.3f}", "-i", right[0],
        "-filter_complex", f"[0:v:0]{side},pad=iw+6:ih:0:0:white[l];[1:v:0]{side}[r];[l][r]hstack=inputs=2[v]",
        "-map", "[v]", "-c:v", "{vcodec}", "-b:v", "4M", "-pix_fmt", "yuv420p", "-an", target,
    )
    return target


def compare(one: Any, other: Any, out: str | Path, *, seconds: float = 4.0, height: int = 360) -> Path:
    """Two shots side by side, each from its middle: the first on the left."""
    cur = db.cursor()
    sides = []
    for shot_id in (one, other):
        cur.execute(
            """SELECT coalesce(a.proxy_path, a.path), s.start_s, s.end_s
                 FROM media_video.shots s JOIN media.assets a ON a.id = s.asset_id WHERE s.id = %s""",
            (shot_id,),
        )
        if not (found := cur.fetchone()):
            raise EditError(f"there is no shot {shot_id}")
        file, start, end = Path(found[0]), float(found[1]), float(found[2])
        if not file.is_file():
            raise EditError(f"{file} is not there to be looked at")
        sides.append((file, max(start, (start + end - seconds) / 2)))
    return side_by_side(sides[0], sides[1], out, seconds=seconds, height=height)


def _even(number: float) -> int:
    return max(2, 2 * round(number / 2))
