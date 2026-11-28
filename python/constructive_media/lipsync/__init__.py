"""Lip sync: a face in a piece of video, made to say something else.

A `Syncer` is given a piece of video with a face in it and a recording of
speech, and writes the video again with the mouth moved to say what the
recording says. Everything else in the picture is left as it was. The
engine asks for one by slot, `lip_sync`, and never knows which model did it.

Nothing in this repository fills the slot. The models that do it well come
with terms of their own, and so do the face models they lean on, so an
adapter for one is a plugin, in
[media-engine-plugins](https://github.com/constructive-io/media-engine-plugins).

    media lipsync face.mp4 line.wav out/face-says-line.mp4

What is written runs as long as the speech does, and the speech under it
is the recording as it was handed over, not a copy that has been through
the model.

A video that ends before the speech does is made to last by the adapter,
each in its own way, and what a face does when its video has run out is
worth looking at: the command says when that happened.

The face is somebody's. A model that moves a mouth will move anybody's, and
a person shown saying what they never said is the harm this is known for.
Use a face that is yours to use: one a model made, or one whose owner said
yes to these words.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import adapters, ffmpeg
from ..settings import Settings

#: What the result is written as. The name of the file says which.
KINDS = (".mp4", ".mov", ".m4v")

#: Sound as every player takes it: some drop one channel at a low rate.
SOUND = ("-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2")

#: A picture and a recording this far apart at the end are as long as each other: two frames of film.
NEAR = 0.1

#: Bits a second for each pixel of each frame, when the adapter's picture has to be written again.
BITS = 0.2


class LipSyncError(RuntimeError):
    """A face could not be made to say a recording."""


@runtime_checkable
class Syncer(Protocol):
    """Something that moves the mouth of a face in a video to say a recording."""

    name: str
    #: What is printed beside what it made, so results can be told apart.
    model: str

    def sync(self, video: Path, speech: Path, target: Path) -> Path:
        """Write `video` to `target` with the mouth of the face in it saying `speech`, and return where it is.

        `target` is an MP4 file. The picture runs as long as the speech: a
        video that is longer is cut to it, and one that is shorter is made
        to last however the model does that. Sound in what is written is
        ignored; whoever asked lays the speech under the picture.
        """
        ...


def syncer(name: str | None = None, *, settings: Settings | None = None) -> Syncer:
    """The chosen syncer, or the configured one."""
    return adapters.get("lip_sync", name, settings=settings)


def sync(
    video: str | Path, speech: str | Path, output: str | Path, *, adapter: str | None = None, syncer: Any = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Write `video` to `output` with the face in it saying `speech`, and say what was written.

    Whatever fills the `lip_sync` slot moves the mouth. The speech laid
    under the picture is the recording as it was handed over. `notes` says
    in words what a person should know before they use it: that the video
    ran out before the speech did, or that the picture came back another
    size.
    """
    source, said, target = Path(video).expanduser(), Path(speech).expanduser(), Path(output).expanduser()
    if not source.is_file():
        raise LipSyncError(f"{source}: no such video")
    if not said.is_file():
        raise LipSyncError(f"{said}: no such recording")
    if target.suffix.lower() not in KINDS:
        raise LipSyncError(f"{target.name}: the name says what kind of file is written, and it is one of "
                           f"{', '.join(KINDS)}")
    if target.exists() and (os.path.samefile(target, source) or os.path.samefile(target, said)):
        raise LipSyncError(f"{target} is what was handed in: write the result somewhere else")
    shown = ffmpeg.probe(source)
    if shown is None or shown.medium != "video" or not shown.duration_s:
        raise LipSyncError(f"{source} holds no picture")
    heard = ffmpeg.probe(said)
    if heard is None or not (heard.has_audio or heard.medium == "audio") or not heard.duration_s:
        raise LipSyncError(f"{said} holds no sound")
    does = syncer if syncer is not None else globals()["syncer"](adapter, settings=settings)
    with tempfile.TemporaryDirectory(prefix="lipsync-") as work:
        began = time.perf_counter()
        made = does.sync(source, said, Path(work) / "synced.mp4")
        took = time.perf_counter() - began
        made = Path(made) if isinstance(made, (str, Path)) else None
        moved = ffmpeg.probe(made) if made is not None and made.is_file() else None
        if moved is None or moved.medium != "video" or not moved.duration_s:
            raise LipSyncError(f"the {does.name} adapter wrote no picture for {source.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _laid(made, moved, said, float(heard.duration_s), target)
    written = ffmpeg.probe(target)
    if written is None or written.medium != "video":
        raise LipSyncError(f"{target} was not written as a video")
    return {
        "video": source.name, "speech": said.name, "output": str(target),
        "seconds": round(float(written.duration_s or 0), 2),
        "video_seconds": round(float(shown.duration_s), 2), "speech_seconds": round(float(heard.duration_s), 2),
        "size": [written.width, written.height], "fps": round(float(written.fps or 0), 2),
        "adapter": does.name, "model": does.model, "took": round(took, 2),
        "notes": _notes(shown, heard, moved),
    }


def _laid(picture: Path, moved: ffmpeg.Probe, speech: Path, seconds: float, target: Path) -> None:
    """Write the adapter's picture with the speech under it, for as long as the speech runs."""
    ends = ("-t", f"{seconds:.3f}", "-movflags", "+faststart", target)
    inputs = ("-i", picture, "-i", speech, "-map", "0:v:0", "-map", "1:a:0")
    try:
        # The picture as the adapter wrote it, where the file can hold it: writing it again costs a little of it.
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-c:v", "copy", *SOUND, *ends)
    except ffmpeg.FfmpegError:
        rate = int((moved.width or 0) * (moved.height or 0) * (moved.fps or 25) * BITS)
        ffmpeg.encode(*inputs, "-c:v", "{vcodec}", "-b:v", str(max(1_000_000, rate)), "-pix_fmt", "yuv420p",
                      *SOUND, *ends)


def _notes(shown: ffmpeg.Probe, heard: ffmpeg.Probe, moved: ffmpeg.Probe) -> list[str]:
    """What a person should be told about a face made to speak before they use it."""
    notes: list[str] = []
    video, speech, picture = float(shown.duration_s or 0), float(heard.duration_s or 0), float(moved.duration_s or 0)
    if video < speech - NEAR:
        notes.append(f"the video ran out {speech - video:.1f} s before the speech did: what the picture does "
                     "after that is the adapter's doing, so look at it")
    if picture < speech - NEAR:
        notes.append(f"the picture that came back ends {speech - picture:.1f} s before the speech does")
    if (moved.width, moved.height) != (shown.width, shown.height):
        notes.append(f"the picture came back at {moved.width}x{moved.height}, where the video was "
                     f"{shown.width}x{shown.height}")
    return notes
