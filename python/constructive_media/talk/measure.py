"""How long the wait is: each part of a turn, timed on its own.

A conversation is as good as its wait, and the wait is three models long.
This times each of them where it runs, twice: the first time, when a model
may still have to be loaded, and again once it is warm, which is what a
conversation feels like after its first turn.

    told = measure.measure(parts)
    told["wait_ms"]     # from the end of your sentence to the first sound

What is counted is what is waited for. Of the answer that is its first
sentence, because that is said while the rest is still being written.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .. import text as texts
from .. import transcription
from . import Parts, sayable, sentences

#: What is said, and listened to when no recording is given.
LINE = "The tide comes in twice a day, and it does not wait for anybody."
#: What is asked when nothing was heard to ask.
QUESTION = "Why is the sea salty?"

_TROUBLE = (RuntimeError, OSError)


def _timed(work: Callable[[], Any]) -> tuple[Any, int]:
    began = time.perf_counter()
    made = work()
    return made, round(1000 * (time.perf_counter() - began))


def _part(name: str, model: Any) -> dict[str, Any]:
    return {"part": name, "adapter": getattr(model, "name", ""), "model": getattr(model, "model", "")}


def measure(parts: Parts, recording: str | Path | None = None) -> dict[str, Any]:
    """Time each part of a turn with these models.

    `recording` is something said, to be written down; without one what is
    listened to is a line the voice has just said. Each part comes back with
    `first_ms`, `ms` once warm, and `trouble` in place of them when it failed.
    `wait_ms` is the sum of the warm times, or None when a part failed.
    """
    with tempfile.TemporaryDirectory(prefix="talk-") as scratch:
        folder = Path(scratch)

        def say(words: str, name: str) -> Path:
            return Path(parts.synthesizer.say(words, folder / name, voice=parts.voice, seed=parts.voice.seed))

        saying = _part("saying", parts.synthesizer)
        line = None
        try:
            line, saying["first_ms"] = _timed(lambda: say(LINE, "line.wav"))
        except _TROUBLE as error:
            saying["trouble"] = str(error)

        hearing, heard = None, ""
        if parts.transcriber is not None:
            hearing = _part("hearing", parts.transcriber)
            source = Path(recording) if recording else line
            if source is None:
                hearing["trouble"] = "there was nothing to listen to: the line could not be said"
            else:
                try:
                    for when in ("first_ms", "ms"):
                        found, hearing[when] = _timed(lambda: transcription.clean(
                            parts.transcriber.transcribe(source, language=parts.language)))
                    heard = " ".join(segment.text.strip() for segment in found).strip()
                    hearing["heard"] = heard
                except _TROUBLE as error:
                    hearing["trouble"] = str(error)

        answering = _part("answering", parts.writer)
        messages = [{"role": "system", "content": parts.system}, {"role": "user", "content": heard or QUESTION}]
        sentence = None
        try:
            for when in ("first_ms", "ms"):
                sentence, answering[when], answering["first_word_ms"] = _first_sentence(parts, messages)
            if sentence:
                answering["said"] = sentence
            else:
                answering["trouble"] = "it answered nothing"
        except _TROUBLE as error:
            answering["trouble"] = str(error)

        if "trouble" not in saying:
            try:
                _, saying["ms"] = _timed(lambda: say(sayable(sentence or "") or LINE, "answer.wav"))
            except _TROUBLE as error:
                saying["trouble"] = str(error)

    told = [part for part in (hearing, answering, saying) if part is not None]
    for part in told:
        if "trouble" in part:
            part.pop("first_ms", None)
            part.pop("ms", None)
    whole = all("ms" in part for part in told)
    return {"parts": told, "wait_ms": sum(part["ms"] for part in told) if whole else None}


def _first_sentence(parts: Parts, messages: list[dict[str, str]]) -> tuple[str | None, int, int | None]:
    """The first sentence of an answer, how long it took, and how long its first word took."""
    began = time.perf_counter()
    first: list[int] = []
    pieces = texts.reply(parts.writer, messages, limit=parts.limit)

    def clocked() -> Iterator[str]:
        for piece in pieces:
            if not first:
                first.append(round(1000 * (time.perf_counter() - began)))
            yield piece

    try:
        sentence = next(sentences(clocked()), None)
    finally:
        pieces.close()
    return sentence, round(1000 * (time.perf_counter() - began)), first[0] if first else None
