"""When someone is speaking: a microphone's sound, cut into the things that were said.

A microphone that is left open sends sound the whole time, and most of it is
a room. What is wanted from it is two moments: when someone starts to speak,
so that whatever is being said back can stop, and when they have finished,
so that what they said can be written down and answered.

    said = hearing.Utterances(hearing.ears(settings)())
    for heard in said.push(sound):
        if heard is None:
            ...     # someone has started to speak
        else:
            ...     # what they said, now that they have stopped

Two things can tell speech from a room. `Silero` is a small model made for
it (Silero VAD, MIT), run through ONNX Runtime, and it is what is used when
ONNX Runtime and NumPy are installed: it knows a voice from a door, a fan or
a keyboard. `Loudness` needs nothing installed and knows only that something
is louder than the room has been, which is enough in a quiet room with a
microphone that is close.
"""

from __future__ import annotations

import functools
import importlib
import math
import sys
from array import array
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from .. import runtime
from ..models import ModelError, Remote, fetch
from ..settings import Settings
from . import RATE

#: How many samples are judged at a time: 32 milliseconds.
FRAME = 512
BYTES = 2 * FRAME

#: Silero VAD (MIT), as an ONNX file.
SILERO = Remote(
    url="https://huggingface.co/onnx-community/silero-vad/resolve/"
        "e71cae966052b992a7eca6b17738916ce0eca4ec/onnx/model.onnx",
    sha256="a4a068cd6cf1ea8355b84327595838ca748ec29a25bc91fc82e6c299ccdc5808",
)
#: How much of the frame before it the model is shown with each frame.
CONTEXT = 64


class Ears(Protocol):
    """Something that can tell whether a frame of sound has speech in it."""

    name: str

    def speech(self, frame: bytes) -> float:
        """How likely it is, from 0 to 1, that someone is speaking in these `FRAME` samples."""

    def reset(self) -> None:
        """Forget what was heard before: what comes next is another microphone."""


def samples(frame: bytes) -> array:
    """The 16-bit samples in `frame`, which are sent least significant byte first."""
    held = array("h")
    held.frombytes(frame)
    if sys.byteorder == "big":
        held.byteswap()
    return held


class Loudness:
    """Speech as whatever is well above how loud the room has been."""

    name = "loudness"

    #: Nothing quieter than this is speech, however quiet the room is, in dB below full scale.
    LEAST = -50.0
    #: How far above the room a frame is when it is as likely speech as not, in dB.
    ABOVE = 12.0
    #: How fast the room is taken to have got louder, in dB a frame. It gets quieter at once.
    RISE = 0.05

    def __init__(self) -> None:
        self._room: float | None = None

    def speech(self, frame: bytes) -> float:
        held = samples(frame)
        if not held:
            return 0.0
        mean = sum(sample * sample for sample in held) / len(held)
        level = 10 * math.log10(mean / 32768.0 ** 2) if mean else -96.0
        room = level if self._room is None else min(level, self._room + self.RISE)
        self._room = room
        if level < self.LEAST:
            return 0.0
        return max(0.0, min(1.0, (level - room - self.ABOVE / 2) / self.ABOVE))

    def reset(self) -> None:
        self._room = None


class Silero:
    """Speech as Silero VAD hears it. `session` is the model, loaded: see `Silero.session`."""

    name = "silero-vad"

    def __init__(self, session: Any) -> None:
        import numpy

        self._numpy, self._session = numpy, session
        self._rate = numpy.array(RATE, dtype=numpy.int64)
        self.reset()

    @staticmethod
    def session(settings: Settings) -> Any:
        """The model, fetched if it has to be and loaded. One will do for every microphone."""
        runtime.available()
        # It takes a tenth of a millisecond a frame on a processor, and nothing else would be quicker.
        return runtime.session(fetch(SILERO, settings.models_dir / "talk" / "silero-vad.onnx"), "cpu")

    def speech(self, frame: bytes) -> float:
        numpy = self._numpy
        heard = numpy.frombuffer(frame, dtype="<i2").astype(numpy.float32) / 32768.0
        shown = numpy.concatenate([self._before, heard])[None, :]
        likely, self._state = self._session.run(None, {"input": shown, "state": self._state, "sr": self._rate})
        self._before = heard[-CONTEXT:]
        return float(likely.reshape(-1)[0])

    def reset(self) -> None:
        numpy = self._numpy
        self._state = numpy.zeros((2, 1, 128), dtype=numpy.float32)
        self._before = numpy.zeros(CONTEXT, dtype=numpy.float32)


def ears(settings: Settings, *, tell: Callable[[str], None] | None = None) -> Callable[[], Ears]:
    """What will tell speech from a room here, as something that makes one for each microphone.

    Silero VAD where it can be run, and loudness where it cannot: without
    ONNX Runtime and NumPy, or when the model is not here and cannot be
    fetched, which `tell` is told.
    """
    try:
        for needed in ("numpy", "onnxruntime"):
            importlib.import_module(needed)
    except ModuleNotFoundError:
        return Loudness
    try:
        return functools.partial(Silero, Silero.session(settings))
    except ModelError as error:
        if tell is not None:
            tell(f"{error} Until then speech is told from the room by how loud it is.")
        return Loudness


def _frames(milliseconds: float) -> int:
    return max(1, math.ceil(milliseconds * RATE / 1000 / FRAME))


class Utterances:
    """A microphone's sound, a piece at a time, as the things that were said into it."""

    def __init__(self, ears: Ears, *, threshold: float = 0.5, speech_ms: float = 250, quiet_ms: float = 600,
                 before_ms: float = 240, longest_s: float = 30) -> None:
        self.ears = ears
        #: Above this a frame is speech. Once someone is speaking, a little under it still is.
        self.threshold = threshold
        self._onset = _frames(speech_ms)
        self._quiet = _frames(quiet_ms)
        self._longest = _frames(1000 * longest_s)
        #: The last of what came before anyone spoke. A word starts before it is loud enough to be known for one.
        self._recent: deque[bytes] = deque(maxlen=_frames(before_ms) + self._onset)
        self._left = b""
        self._said: list[bytes] | None = None
        self._running = self._silent = 0

    @property
    def speaking(self) -> bool:
        """Whether someone is in the middle of saying something."""
        return self._said is not None

    def push(self, sound: bytes) -> Iterator[bytes | None]:
        """Take more of the microphone's sound: 16-bit samples, one channel, `RATE` a second.

        Gives None the moment someone has started to speak, and what they
        said, in the same form, once they have stopped.
        """
        sound = self._left + sound
        whole = len(sound) - len(sound) % BYTES
        self._left = sound[whole:]
        for at in range(0, whole, BYTES):
            frame = sound[at:at + BYTES]
            likely = self.ears.speech(frame)
            if self._said is None:
                self._recent.append(frame)
                self._running = self._running + 1 if likely >= self.threshold else 0
                if self._running >= self._onset:
                    self._said, self._silent = list(self._recent), 0
                    self._recent.clear()
                    yield None
                continue
            self._said.append(frame)
            self._silent = self._silent + 1 if likely < self.threshold - 0.15 else 0
            if self._silent >= self._quiet or len(self._said) >= self._longest:
                said, self._said, self._running = b"".join(self._said), None, 0
                yield said

    def reset(self) -> None:
        """Drop whatever is half heard."""
        self._left, self._said, self._running, self._silent = b"", None, 0, 0
        self._recent.clear()
        self.ears.reset()
