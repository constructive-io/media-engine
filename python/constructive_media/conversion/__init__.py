"""Voice conversion: what one voice said, said again in another.

A `Converter` is given a recording of somebody speaking and a clip of
somebody else, and writes the recording again in the second voice: the same
words at the same pace, with the same rises and falls, and another person's
sound. The engine asks for one by slot, `voice_conversion`, and never knows
which model did it.

Nothing in this repository fills the slot. The models that do it well are
under licenses the engine's own code may not carry, so an adapter for one
is a plugin, in
[media-engine-plugins](https://github.com/constructive-io/media-engine-plugins).

It is used two ways. A recording is said again in a voice:

    media revoice take.wav out/take-as-ada.wav --voice ada

And a voice that takes seconds to clone a sentence is reached another way
when talking, where seconds are a long time: the sentence is said in a
quick voice, and that is said again in the one asked for. `Respoken` is
the synthesizer that does it, made of a synthesizer and a converter:

    media talk --voice ada --quick af_heart
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings
from ..speech import SpeechError, Synthesizer, Voice


class ConversionError(SpeechError):
    """A recording could not be said again in another voice."""


@runtime_checkable
class Converter(Protocol):
    """Something that says a recording again in another voice."""

    name: str
    #: What is recorded beside what it made, so renderings can be told apart.
    model: str

    def convert(self, source: Path, target: Path, *, like: Path) -> Path:
        """Write what is said in `source`, in the voice heard in `like`, to `target` as a WAV file, and return it.

        `like` is a clip of the voice alone, a few seconds or more. The file
        written may be at another rate than the source: whoever asked brings
        it back.
        """
        ...


def converter(name: str | None = None, *, settings: Settings | None = None) -> Converter:
    """The chosen converter, or the configured one."""
    return adapters.get("voice_conversion", name, settings=settings)


def clip(voice: Voice) -> Path:
    """The recording a voice was cloned from, which is what a converter hears it by."""
    if not voice.reference_audio:
        raise ConversionError(f"the {voice.emotion} voice of {voice.persona!r} was not made from a clip, so there "
                              "is nothing to hear it by. Words are said again in a voice that has a recording")
    held = Path(voice.reference_audio).expanduser()
    if not held.is_file():
        raise ConversionError(f"the clip the {voice.emotion} voice of {voice.persona!r} was made from is not "
                              f"there: {held}")
    return held


class Respoken:
    """A synthesizer of two parts: words are said in a quick voice, and said again in the voice asked for.

    `first` says the words in `quick`, a voice it has of its own, and
    `converter` says that again in the voice a line is asked in, which has to
    be one made from a clip. What comes out has the quick voice's reading of
    the line, its pace and its stresses, and the other voice's sound.
    """

    def __init__(self, first: Synthesizer, quick: Voice, converter: Converter) -> None:
        self.first = first
        self.quick = quick
        self.converter = converter
        self.name = f"{first.name}+{converter.name}"
        self.model = f"{first.model} + {converter.model}"

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        like = clip(voice)
        with tempfile.TemporaryDirectory(prefix="respoken-") as folder:
            said = self.first.say(text, Path(folder) / "said.wav", voice=self.quick, seed=seed)
            return Path(self.converter.convert(Path(said), Path(target), like=like))
