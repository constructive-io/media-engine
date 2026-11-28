"""Separation: a recording pulled apart into the voice in it and what was under the voice.

A `Separator` is given a recording of several things at once, somebody
speaking or singing over music, and writes each of them as a file of its
own. One of the parts is always `voice`. The engine asks for one by slot,
`separation`, and never knows which model did it.

Nothing in this repository fills the slot. The models that do it well were
trained on music whose terms do not reach as far as the engine's own, so an
adapter for one is a plugin, in
[media-engine-plugins](https://github.com/constructive-io/media-engine-plugins).

    media separate song.wav parts

That writes `parts/voice.wav` and `parts/rest.wav`, or more parts when the
model keeps more apart, and says how loud each is beside the recording.
It is what comes before `media clean` and `media reference` when the only
recording of a voice has music under it: a denoiser takes off what is steady,
and music is not.

What is pulled apart is never quite whole. A voice that came from under
music has a little of the music left in it and has lost a little of itself,
and how much depends on the recording. Listen to it before a voice is made
from it.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings

#: The part every separator writes, and the name of everything else when it is kept as one.
VOICE = "voice"
REST = "rest"

#: A part's name is the name of its file.
NAME = re.compile(r"[a-z][a-z0-9_-]*")

#: A part this far under the recording has nothing in it worth the name.
EMPTY = 40.0


class SeparationError(RuntimeError):
    """A recording could not be pulled apart."""


@runtime_checkable
class Separator(Protocol):
    """Something that pulls a recording apart."""

    name: str
    #: What is recorded beside what it made, so separations can be told apart.
    model: str

    def separate(self, source: Path, folder: Path) -> dict[str, Path]:
        """Write the parts of `source` into `folder`, each a WAV file, and return where each is by its name.

        One part is `voice`: whoever speaks or sings. The others are what
        the model keeps apart: `rest` for everything else as one, or a name
        for each. The parts run as long as the recording and add up to it,
        near enough. They may be at another rate than the source.
        """
        ...


def separator(name: str | None = None, *, settings: Settings | None = None) -> Separator:
    """The chosen separator, or the configured one."""
    return adapters.get("separation", name, settings=settings)


def apart(
    recording: str | Path, folder: str | Path, *, adapter: str | None = None, separator: Any = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Pull `recording` apart and write its parts into `folder`, one WAV file each, named for the part.

    Whatever fills the `separation` slot does it. The answer says how loud
    the recording is and how loud each part is, as the root mean square of
    its samples in dB, so that a part with nothing in it is seen to have
    nothing in it: `notes` says so in words.
    """
    from ..audio import mix, pcm

    source = Path(recording).expanduser()
    if not source.is_file():
        raise SeparationError(f"{source}: no such recording")
    if not pcm.seconds(source):
        raise SeparationError(f"{source} holds no sound")
    folder = Path(folder).expanduser()
    if folder.exists() and not folder.is_dir():
        raise SeparationError(f"{folder} is a file: the parts are written into a folder")
    does = separator if separator is not None else globals()["separator"](adapter, settings=settings)
    whole = mix.loudness(pcm.read(source, stereo=False))
    parts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="separate-") as work:
        began = time.perf_counter()
        made = does.separate(source, Path(work))
        took = time.perf_counter() - began
        if not isinstance(made, dict) or VOICE not in made:
            raise SeparationError(f"the {does.name} adapter gave back no part called {VOICE}")
        if len(made) < 2:
            raise SeparationError(f"the {does.name} adapter gave back the {VOICE} and nothing it was apart from")
        for name, held in made.items():
            if not NAME.fullmatch(str(name)):
                raise SeparationError(f"the {does.name} adapter gave a part a name that is not one: {name!r}")
            held = Path(held)
            if not held.is_file() or not pcm.seconds(held):
                raise SeparationError(f"the {does.name} adapter wrote nothing for the {name} of {source.name}")
        folder.mkdir(parents=True, exist_ok=True)
        # The voice first, then the others as the adapter gave them.
        for name in sorted(made, key=lambda one: one != VOICE):
            target = folder / f"{name}.wav"
            shutil.copyfile(made[name], target)
            parts.append({"part": name, "file": str(target), "seconds": round(float(pcm.seconds(target) or 0), 2),
                          "level": round(mix.loudness(pcm.read(target, stereo=False)), 1)})
    return {
        "recording": source.name, "level": round(whole, 1), "folder": str(folder), "parts": parts,
        "adapter": does.name, "model": does.model, "took": round(took, 2), "notes": _notes(whole, parts),
    }


def _notes(whole: float, parts: list[dict[str, Any]]) -> list[str]:
    """What a person should be told about a separation before they use it."""
    voice = next(one for one in parts if one["part"] == VOICE)
    others = [one for one in parts if one["part"] != VOICE]
    if voice["level"] < whole - EMPTY:
        return ["hardly anything came out as the voice: no voice was found in the recording"]
    if all(one["level"] < whole - EMPTY for one in others):
        return ["hardly anything came out but the voice: nothing was found under it, and the recording may as well "
                "be used as it is"]
    return []
