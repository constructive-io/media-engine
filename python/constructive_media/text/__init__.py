"""Text models: one that reads and answers, and one that turns words into numbers.

A `Writer` is given a prompt and returns what a model wrote back. An
`Embedder` is given pieces of text and returns a list of numbers for each,
near one another for texts that mean much the same. The engine asks for
either by slot, `text` or `embedding`, and never knows which model or which
program answered.

Neither is asked for facts. A writer is asked for an opinion of words the
engine already holds, and what comes back is read as an opinion: a number
that is out of range is brought into it, and a reply that cannot be read is
counted and left out, not guessed at.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings


class TextError(RuntimeError):
    """A text model could not be reached, or did not answer."""


@runtime_checkable
class Writer(Protocol):
    """Something that reads a prompt and writes an answer."""

    name: str
    #: What is recorded beside whatever it judged, so opinions can be told apart.
    model: str

    def write(self, prompt: str, *, limit: int = 200, as_json: bool = False) -> str:
        """What the model wrote in answer to `prompt`, in at most `limit` tokens.

        With `as_json` the model is asked to answer with a JSON object and
        nothing else. It may still not, and `read` allows for that.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """Something that turns text into numbers that are close for like meanings."""

    name: str
    #: What is recorded beside every embedding. Numbers from two models are not
    #: in the same space and are never compared.
    model: str

    def embed(self, texts: Sequence[str], *, asking: bool = False) -> list[list[float]]:
        """A list of numbers for each of `texts`, in the same order.

        `asking` says the texts are questions to find answers to, which some
        models want to be told.
        """
        ...


def writer(name: str | None = None, *, settings: Settings | None = None) -> Writer:
    """The chosen writer, or the configured default."""
    return adapters.get("text", name, settings=settings)


def embedder(name: str | None = None, *, settings: Settings | None = None) -> Embedder:
    """The chosen embedder, or the configured default."""
    return adapters.get("embedding", name, settings=settings)


def read(reply: str | None) -> dict[str, Any] | None:
    """The first JSON object in a reply, or None when there is none.

    A model asked for an object often wraps it in a sentence or writes two.
    The first one that parses is taken, from wherever it starts.
    """
    reply = reply or ""
    decoder = json.JSONDecoder()
    at = reply.find("{")
    while at >= 0:
        try:
            found, _ = decoder.raw_decode(reply[at:])
        except ValueError:
            found = None
        if isinstance(found, dict):
            return found
        at = reply.find("{", at + 1)
    return None


def share(value: Any) -> float | None:
    """A model's number as a share from 0 to 1, or None when it is not a number.

    Models write numbers as text as often as not, and now and then write one
    that is out of range.
    """
    try:
        found = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if found != found:
        return None
    return max(0.0, min(1.0, found))
