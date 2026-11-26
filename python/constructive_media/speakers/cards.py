"""A few seconds of each voice, to listen to before naming it.

A card is the moments a voice is most itself, one after another with a breath
between them, in one sound file. It is cut from the recording when its voices
are found and kept in the work folder, beside the frames and proxies. It is
something to listen to, not part of the library, and is made again whenever
the voices are.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..settings import Settings
from .voices import WINDOW_S, Voice

#: Seconds over which each moment fades in and out, so the cuts do not click.
FADE_S = 0.04
#: Seconds of quiet after each moment.
BREATH_S = 0.35


def folder(asset_id: Any, settings: Settings | None = None) -> Path:
    """Where a recording's cards are kept."""
    settings = settings or Settings.from_env()
    return settings.work_dir / "speakers" / str(asset_id)[:8]


def card(asset_id: Any, label: str, settings: Settings | None = None) -> Path | None:
    """A voice's card, if it has been cut."""
    path = folder(asset_id, settings) / f"{label}.wav"
    return path if path.is_file() else None


def cut(source: str | Path, starts: Sequence[float], target: Path, *, length_s: float = WINDOW_S) -> Path:
    """Write the moments of `source` beginning at `starts` to one sound file."""
    inputs: list[str] = []
    steps = []
    for index, start in enumerate(starts):
        inputs += ["-ss", f"{start:.3f}", "-t", f"{length_s:.3f}", "-i", str(source)]
        steps.append(
            f"[{index}:a:0]aresample=48000,aformat=channel_layouts=mono,"
            f"afade=t=in:d={FADE_S},afade=t=out:st={length_s - FADE_S:.3f}:d={FADE_S},"
            f"apad=pad_dur={BREATH_S}[moment{index}]"
        )
    joined = "".join(f"[moment{index}]" for index in range(len(starts)))
    steps.append(f"{joined}concat=n={len(starts)}:v=0:a=1[card]")
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *inputs,
               "-filter_complex", ";".join(steps), "-map", "[card]", "-c:a", "pcm_s16le", target)
    return target


def make(asset_id: Any, source: str | Path, voices: Sequence[Voice],
         settings: Settings | None = None) -> list[Path]:
    """Cut a card for each voice of a recording, in place of the ones it had."""
    into = folder(asset_id, settings)
    if into.is_dir():
        shutil.rmtree(into)
    return [cut(source, voice.best, into / f"{voice.label}.wav") for voice in voices if voice.best]
