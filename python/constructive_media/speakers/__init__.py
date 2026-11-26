"""Speakers: who is talking, and when.

A transcript says what was said. This says by whom, in three steps:

- `voices`   a recording's speech is cut into short windows, each window is
             turned into numbers by an `Embedder`, and windows that sound like
             one person are grouped. Each group is a voice: `voice1`, `voice2`.
- `cards`    a few seconds of each voice, to listen to.
- `names`    someone says whose voice it is, once.

Nothing here knows who anyone is until they are named. A voice that has been
named is recognised when it turns up in another recording.

An `Embedder` is the one model in this: given pieces of sound, it answers with
a row of numbers for each, close together for one person and apart for two.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings

#: The sample rate every embedder is given sound at.
RATE = 16000
#: The least sound, in seconds, a voice can be told from.
SHORTEST_S = 0.4


class SpeakerError(LookupError):
    """A recording, voice or person that was asked for is not there, or not ready."""


@runtime_checkable
class Embedder(Protocol):
    """Something that can turn speech into numbers that follow the speaker."""

    name: str
    #: What is recorded beside each voice. Numbers from two models cannot be compared.
    model: str

    def embed(self, sounds: Sequence[Any]) -> Any:
        """One row for each sound, at unit length, in the order they were given.

        A sound is one channel at `RATE`, as floats from -1 to 1, at least
        `SHORTEST_S` long. The answer is a numpy array, `sounds x dimensions`.
        """
        ...


def embedder(name: str | None = None, *, settings: Settings | None = None) -> Embedder:
    """The chosen embedder, or the configured default."""
    return adapters.get("speaker", name, settings=settings)
