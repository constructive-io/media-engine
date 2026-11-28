"""A speech model in a process of its own, with a limit on how long a line may take.

A model on a graphics processor can stop without failing. It does not raise
and it does not finish: it sits, for hours, and whatever was waiting for the
line waits with it. A script rendered overnight is found in the morning one
line in.

Nothing inside a process can stop that, because the process is the thing
that is stuck. So the model is run in another one. This side sends it a line
and waits for the answer, and when no answer comes in time the other process
is killed and a new one started for the next line. To whatever asked, a line
that ran out of time is a line that failed, and a failed line is said again
with another seed like any other.

    worker = Apart("constructive_media.speech.mlx:MlxSpeech", patience=120)
    worker.call("say", text="Bring the boats in.", target="line.wav", voice={...}, seed=7)

Three things it is careful of, each learned from a night that went wrong:

- The clock for a line starts when the other process holds the lock on the
  graphics processor, not when the line is sent. Waiting a turn behind another
  `media` is not being stuck.
- The other process is killed before its pipes are closed. Closing a pipe
  that a thread is in the middle of reading from can wait for ever on that
  read, and then the thing that was meant to end the wait is part of it.
- Answers come back on a pipe of their own. What a model's loader prints to
  the screen would otherwise turn up between two answers.

The other process is this module, run by name: `python -m
constructive_media.speech.apart MODULE:CLASS`.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import IO, Any

from .. import gpu
from ..adapters import lazy
from . import SpeechError, Voice

#: The variable the other process finds its answering pipe in.
ANSWER_FD = "CONSTRUCTIVE_MEDIA_ANSWER_FD"
#: What may be asked of the other process.
ASKED = ("say", "say_as", "embed")
#: A model is given this many times a line's patience to load, and no less than a quarter of an hour:
#: the first time, it is fetched.
STARTING_TIMES = 4
STARTING_LEAST_S = 900.0
#: How much of what the other process wrote before it died is told.
LAST_WORDS = 400


class Apart:
    """A synthesizer in a process of its own. Built by name, started when first asked."""

    def __init__(self, maker: str, *, patience: float, starting: float | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        if patience <= 0:
            raise SpeechError("a line has to be given some time: patience is in seconds, and more than none")
        self.maker = maker
        self.patience = float(patience)
        self.starting = float(starting) if starting else max(self.patience * STARTING_TIMES, STARTING_LEAST_S)
        self._env = dict(os.environ if env is None else env)
        self._process: subprocess.Popen[bytes] | None = None
        self._answers: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._errors: IO[bytes] | None = None
        #: How many times the other process had to be stopped for taking too long.
        self.stopped = 0
        atexit.register(self.close)

    # Asking.

    def call(self, what: str, **arguments: Any) -> Any:
        """Ask the other process for one thing and wait for it, no longer than a line is given."""
        if what not in ASKED:
            raise SpeechError(f"a speech model in a process of its own is not asked to {what!r}")
        process = self._started()
        try:
            assert process.stdin is not None
            process.stdin.write((json.dumps({"do": what, **arguments}) + "\n").encode())
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise SpeechError(self._died()) from None
        # Its turn on the graphics processor is waited for as long as it takes.
        self._answer(None, "began")
        answer = self._answer(self.patience, "done")
        return answer.get("value")

    def close(self) -> None:
        """Stop the other process. It is started again if anything more is asked."""
        process, self._process = self._process, None
        if process is None:
            return
        # Killed first: that ends whatever a thread is reading from it, and only then are its pipes closed.
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=10)
        for pipe in (process.stdin,):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        if self._errors is not None:
            with contextlib.suppress(OSError):
                self._errors.close()
            self._errors = None

    # The other process.

    def _started(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self.close()
        reading, writing = os.pipe()
        self._errors = tempfile.TemporaryFile()  # noqa: SIM115 - kept until the process is closed
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", __name__, self.maker], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=self._errors, pass_fds=(writing,), env={**self._env, ANSWER_FD: str(writing)})
        finally:
            os.close(writing)
        self._process = process
        answers: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._answers = answers
        threading.Thread(target=_read, args=(os.fdopen(reading, "rb"), answers), daemon=True).start()
        self._answer(self.starting, "ready", doing="loading")
        return process

    def _answer(self, within: float | None, wanted: str, *, doing: str = "saying a line") -> dict[str, Any]:
        try:
            answer = self._answers.get(timeout=within)
        except queue.Empty:
            self.stopped += 1
            self.close()
            raise SpeechError(f"the speech model was still {doing} after {within:g} seconds. It was stopped, and "
                              "is started again for the next line") from None
        if answer is None:
            raise SpeechError(self._died())
        if "error" in answer:
            raise SpeechError(str(answer["error"]))
        if wanted not in answer:
            self.close()
            raise SpeechError(f"the speech model's process answered out of turn: {answer}")
        return answer

    def _died(self) -> str:
        process, last = self._process, ""
        if self._errors is not None:
            with contextlib.suppress(OSError, ValueError):
                self._errors.seek(0)
                last = self._errors.read().decode(errors="replace").strip()[-LAST_WORDS:]
        code = process.poll() if process is not None else None
        self.close()
        return "the speech model's process stopped" + (f" ({code})" if code is not None else "") + \
            (f": {last}" if last else "")


def _read(answers_from: IO[bytes], into: queue.Queue[dict[str, Any] | None]) -> None:
    """Hand each answer on as it comes, and None when there will be no more."""
    with answers_from:
        for line in answers_from:
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            if isinstance(answer, dict):
                into.put(answer)
    into.put(None)


# The other side: what runs in the process of its own.


def voice_from(given: Mapping[str, Any]) -> Voice:
    """A voice, from what was sent of it."""
    known = {field.name for field in fields(Voice)}
    return Voice(**{name: value for name, value in given.items() if name in known})


def do(synthesizer: Any, asked: Mapping[str, Any]) -> Any:
    """Do the one thing asked, and give what there is to send back."""
    what = asked.get("do")
    if what == "say":
        synthesizer.say(asked["text"], Path(asked["target"]), voice=voice_from(asked["voice"]),
                        seed=asked.get("seed"))
        return None
    if what == "say_as":
        synthesizer.say_as(asked["text"], Path(asked["target"]), embedding=asked["embedding"],
                           seed=asked.get("seed"))
        return None
    if what == "embed":
        return synthesizer.embed(asked["recording"])
    raise SpeechError(f"a speech model in a process of its own is not asked to {what!r}")


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1 or ANSWER_FD not in os.environ:
        print("this is run by the engine, for a speech model in a process of its own", file=sys.stderr)
        return 2
    answers = os.fdopen(int(os.environ[ANSWER_FD]), "w", buffering=1)

    def answer(**said: Any) -> None:
        answers.write(json.dumps(said) + "\n")
        answers.flush()

    try:
        synthesizer = lazy(arguments[0])()
        loaded = getattr(synthesizer, "loaded", None)
        if callable(loaded):
            with gpu.alone():
                loaded()
    except Exception as error:  # noqa: BLE001 - whatever stops a model loading is told to whoever asked
        answer(error=str(error) or type(error).__name__)
        return 1
    answer(ready=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            asked = json.loads(line)
            with gpu.alone():
                answer(began=True)
                value = do(synthesizer, asked)
        except Exception as error:  # noqa: BLE001 - a line that fails is an answer, and the next line is still said
            answer(error=str(error) or type(error).__name__)
            continue
        answer(done=True, value=value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
