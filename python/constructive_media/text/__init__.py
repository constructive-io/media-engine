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

A writer may also be talked with: `reply` hands one a conversation and gives
back what it says next, in pieces as they are written, so that the first
sentence can be said aloud while the second is still being thought of.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Settings


class TextError(RuntimeError):
    """A text model could not be reached, or did not answer."""


@runtime_checkable
class Writer(Protocol):
    """Something that reads a prompt and writes an answer.

    One that can hold a conversation, answering as it goes, also has
    `reply(messages, *, limit)`: see `reply` below.
    """

    name: str
    #: What is recorded beside whatever it judged, so opinions can be told apart.
    model: str

    def write(self, prompt: str, *, limit: int = 200, as_json: bool = False) -> str:
        """What the model wrote in answer to `prompt`, in at most `limit` tokens.

        With `as_json` the model is asked to answer with a JSON object and
        nothing else. It may still not, and `read` allows for that.
        """
        ...


def reply(writer: Writer, messages: Sequence[dict[str, str]], *, limit: int = 400) -> Iterator[str]:
    """What a writer says next in a conversation, in pieces, each as it is written.

    `messages` is the conversation so far, oldest first: each has a `role`,
    which is `system`, `user` or `assistant`, and its `content`. A writer
    that can answer as it goes has `reply(messages, *, limit)` and is asked
    that way. One that cannot is handed the conversation as one prompt, and
    its answer comes whole, as a single piece.
    """
    piecewise = getattr(writer, "reply", None)
    if piecewise is not None:
        yield from piecewise(messages, limit=limit)
        return
    written = writer.write(as_prompt(messages), limit=limit)
    if written:
        yield written


def as_prompt(messages: Sequence[dict[str, str]]) -> str:
    """A conversation as one prompt, for a writer that is only ever asked one thing."""
    told = [message["content"] for message in messages if message["role"] == "system"]
    said = [f"{'They' if message['role'] == 'user' else 'You'} said: {message['content']}"
            for message in messages if message["role"] != "system"]
    return "\n\n".join([*told, "\n".join(said), "What you say next, and nothing else:"])


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
