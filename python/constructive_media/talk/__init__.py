"""Talking with a text model aloud: you speak, it answers, and the answer is said.

Three slots do the work, the same three everything else uses:
`transcription` writes down what you said, `text` answers it and `speech`
says the answer. Which model fills each is a setting, as it is everywhere,
so the same conversation can be had with whatever is quickest on the machine
it runs on.

What matters is the wait. An answer that is right in three seconds is worse
to talk with than one that is good enough in one, because by then the person
has started speaking again. So an answer is said a sentence at a time: the
first sentence is being said while the model is still writing the second,
and what is waited for is one sentence, not the whole answer.

And an answer can be talked over. Whatever is said or typed while the model
is answering stops it there, so a wrong answer never has to be heard out.

    parts = talk.Parts(writer=writer, synthesizer=synthesizer, voice=voice, transcriber=transcriber)
    conversation = talk.Conversation(parts, tell)
    conversation.typed("What is the tide doing?")

`tell` is handed everything that happens, in order. A sentence of the answer
comes as the bytes of a WAV file. Everything else comes as a dict:

    {"type": "state", "value": "listening"}   or "thinking", or "speaking"
    {"type": "heard", "text": "..."}          what was said to it, as it was written down
    {"type": "said", "text": "..."}           a sentence of the answer; its sound follows
    {"type": "hush"}                          stop playing: the answer was talked over
    {"type": "timing", ...}                   how long each part of the turn took, in milliseconds
    {"type": "trouble", "text": "..."}        a part failed, and why
    {"type": "done"}                          the turn is over
"""

from __future__ import annotations

import re
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import text as texts
from .. import transcription
from ..speech import Voice

#: What a microphone is listened to at: one channel of 16-bit samples, this many a second.
RATE = 16_000

#: What the model is told it is doing, unless it is told something else.
SYSTEM = (
    "You are talking with someone aloud: what you write is said to them in a voice. "
    "So write the way people speak. Plain sentences, with no lists, no headings, no markdown, no emoji "
    "and nothing that only makes sense on a page. Keep an answer to a sentence or two unless more is asked for."
)
#: How many turns back the model is shown. Every turn kept makes the next answer start later.
REMEMBERED = 12
#: The most tokens an answer may run to.
LIMIT = 400

OPEN, CLOSE = "<think>", "</think>"
#: Where a sentence ends: a stop and then a space, or a stop of the kind that is written with no space after it.
_END = re.compile(r"([.!?…]+)[\"'”’)\]*_]*\s+|[。？！]+[”’」』）]*\s*")
_LAST_WORD = re.compile(r"(\S+)$")
#: Short words that end in a full stop without ending anything.
_ABBREVIATIONS = frozenset("mr mrs ms dr prof st vs inc ltd jr sr".split())
_MARKS = re.compile(r"[*_`#~]+")
_SAYABLE = re.compile(r"\w")


class TalkError(RuntimeError):
    """A conversation could not be had."""


@dataclass
class Parts:
    """What a conversation is had with: the three models, and the voice that answers.

    With no `transcriber` nothing is listened to, and a turn can only be typed.
    """

    writer: Any
    synthesizer: Any
    voice: Voice
    transcriber: Any = None
    system: str = SYSTEM
    language: str = "en"
    limit: int = LIMIT
    remembered: int = REMEMBERED


class Unthought:
    """An answer with the thinking taken out of it, as it comes.

    Some models write what they are thinking between `<think>` and
    `</think>` before they answer, asked to or not. None of that is for
    saying aloud.
    """

    def __init__(self) -> None:
        self._held, self._thinking = "", False

    def push(self, piece: str) -> str:
        """What of the answer so far can be passed on, now that `piece` has come."""
        self._held += piece
        out: list[str] = []
        while True:
            mark = CLOSE if self._thinking else OPEN
            at = self._held.find(mark)
            if at >= 0:
                if not self._thinking:
                    out.append(self._held[:at])
                self._held, self._thinking = self._held[at + len(mark):], not self._thinking
                continue
            # No whole mark. All of it goes but an ending that may be the start of one.
            kept = _beginning(self._held, mark)
            if not self._thinking:
                out.append(self._held[:len(self._held) - kept])
            self._held = self._held[len(self._held) - kept:]
            return "".join(out)

    def rest(self) -> str:
        """What was held back in case it began a mark, and did not."""
        rest, self._held = ("" if self._thinking else self._held), ""
        return rest


def _beginning(text: str, mark: str) -> int:
    """How much of the end of `text` could be the start of `mark`."""
    for length in range(min(len(mark) - 1, len(text)), 0, -1):
        if text.endswith(mark[:length]):
            return length
    return 0


def sentences(pieces: Iterable[str]) -> Iterator[str]:
    """Whole sentences out of an answer that comes in pieces, each the moment it closes."""
    held, answer = "", Unthought()
    for piece in pieces:
        held += answer.push(piece)
        while (cut := _closes(held)) is not None:
            sentence, held = held[:cut].strip(), held[cut:]
            if sentence:
                yield sentence
    last = (held + answer.rest()).strip()
    if last:
        yield last


def _closes(held: str) -> int | None:
    """Where the first sentence in `held` ends, or None when none has ended yet."""
    for end in _END.finditer(held):
        word = _LAST_WORD.search(held[:end.start()])
        last = word.group(1).lower().lstrip("\"'“‘([") if word else ""
        # "Dr. Okafor", "J. Okafor", "3. Then stir" and "e.g. this" go on past the stop.
        if end.group(1) == "." and (last in _ABBREVIATIONS or last.isdigit() or "." in last
                                    or (len(last) == 1 and last.isalpha())):
            continue
        return end.end()
    return None


def sayable(sentence: str) -> str:
    """A sentence as it is to be said: without the marks a page would have shown, or nothing at all."""
    said = " ".join(_MARKS.sub("", sentence).split())
    return said if _SAYABLE.search(said) else ""


def wav(sound: bytes, rate: int = RATE) -> bytes:
    """One channel of 16-bit samples as the bytes of a WAV file."""
    import io

    held = io.BytesIO()
    with wave.open(held, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(sound)
    return held.getvalue()


def _ms(seconds: float) -> int:
    return round(1000 * seconds)


@dataclass
class _Turn:
    stopped: threading.Event
    thread: threading.Thread | None = None


class Conversation:
    """One conversation: what has been said so far, and the turn being answered now."""

    def __init__(self, parts: Parts, tell: Callable[[dict[str, Any] | bytes], None]) -> None:
        self.parts = parts
        self._tell = tell
        self._history: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._turn: _Turn | None = None
        #: Nothing has been said since the last hush, so there is nothing to hush.
        self._quiet = True

    # What comes in.

    def typed(self, said: str) -> None:
        """Take a turn that was typed."""
        if said.strip():
            self._begin(said=said.strip())

    def heard(self, sound: bytes) -> None:
        """Take a turn that was spoken: one channel of 16-bit samples, `RATE` a second."""
        if sound:
            self._begin(sound=sound)

    def hush(self) -> None:
        """Stop the answer where it is."""
        with self._lock:
            self._stop()
            self._tell({"type": "state", "value": "listening"})

    def close(self) -> None:
        """The conversation is over: whatever is being answered is dropped, and nothing more is told."""
        with self._lock:
            if self._turn is not None:
                self._turn.stopped.set()
            self._tell = lambda told: None

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the turn in hand to be over. False when it is not, after `timeout` seconds."""
        with self._lock:
            turn = self._turn
        if turn is None or turn.thread is None:
            return True
        turn.thread.join(timeout)
        return not turn.thread.is_alive()

    @property
    def history(self) -> list[dict[str, str]]:
        """What has been said so far, oldest first."""
        return list(self._history)

    # A turn.

    def _stop(self) -> None:
        if self._turn is not None:
            self._turn.stopped.set()
        if not self._quiet:
            self._quiet = True
            self._tell({"type": "hush"})

    def _begin(self, *, said: str | None = None, sound: bytes | None = None) -> None:
        with self._lock:
            before = self._turn
            self._stop()
            turn = self._turn = _Turn(threading.Event())
            turn.thread = threading.Thread(target=self._run, args=(turn, before, said, sound), daemon=True)
            self._tell({"type": "state", "value": "thinking"})
        turn.thread.start()

    def _say(self, turn: _Turn, told: dict[str, Any] | bytes) -> bool:
        """Tell something of a turn, unless the turn has been stopped. Whether it was told."""
        with self._lock:
            if turn.stopped.is_set():
                return False
            if isinstance(told, bytes):
                self._quiet = False
            self._tell(told)
            return True

    def _run(self, turn: _Turn, before: _Turn | None, said: str | None, sound: bytes | None) -> None:
        began = time.perf_counter()
        timing: dict[str, Any] = {"type": "timing"}
        try:
            if said is None:
                said, took = self._written(sound or b"")
                timing["heard_ms"] = _ms(took)
            # What the turn before said, it has to have finished saying: this answer follows it.
            if before is not None and before.thread is not None:
                before.thread.join()
            if not said:
                return
            # Words are kept even when the answer to them was talked over: the next answer is to them too.
            with self._lock:
                self._tell({"type": "heard", "text": said})
            self._history.append({"role": "user", "content": said})
            if not turn.stopped.is_set():
                self._answer(turn, began, timing)
                self._say(turn, timing)
        except Exception as error:  # noqa: BLE001 - a turn that fails without a word leaves someone waiting
            known = isinstance(error, RuntimeError | OSError)
            self._say(turn, {"type": "trouble", "text": str(error) if known else f"{type(error).__name__}: {error}"})
        finally:
            if self._say(turn, {"type": "done"}):
                self._say(turn, {"type": "state", "value": "listening"})

    def _written(self, sound: bytes) -> tuple[str, float]:
        """What was said in `sound`, and how long it took to write down."""
        if self.parts.transcriber is None:
            raise TalkError("nothing is listening here: this conversation can only be typed")
        with tempfile.TemporaryDirectory(prefix="talk-") as folder:
            heard = Path(folder) / "heard.wav"
            heard.write_bytes(wav(sound))
            began = time.perf_counter()
            found = transcription.clean(self.parts.transcriber.transcribe(heard, language=self.parts.language))
            return " ".join(segment.text.strip() for segment in found).strip(), time.perf_counter() - began

    def _messages(self) -> list[dict[str, str]]:
        """The conversation as the model is shown it: what it is doing, then the last of what was said."""
        messages = [{"role": "system", "content": self.parts.system}]
        for message in self._history[-2 * self.parts.remembered:]:
            if messages[-1]["role"] == message["role"]:
                # Two turns running from one side, when an answer was talked over before it began.
                messages[-1] = {**messages[-1], "content": f"{messages[-1]['content']}\n{message['content']}"}
            else:
                messages.append(dict(message))
        return messages

    def _answer(self, turn: _Turn, began: float, timing: dict[str, Any]) -> None:
        asked = time.perf_counter()
        pieces = texts.reply(self.parts.writer, self._messages(), limit=self.parts.limit)

        def clocked() -> Iterator[str]:
            for piece in pieces:
                timing.setdefault("first_word_ms", _ms(time.perf_counter() - asked))
                yield piece

        told: list[str] = []
        try:
            for sentence in sentences(clocked()):
                if turn.stopped.is_set():
                    break
                timing.setdefault("first_sentence_ms", _ms(time.perf_counter() - asked))
                words = sayable(sentence)
                if not words:
                    continue
                sound = self._sound(words)
                if not told and not self._say(turn, {"type": "state", "value": "speaking"}):
                    break
                if not (self._say(turn, {"type": "said", "text": words}) and self._say(turn, sound)):
                    break
                timing.setdefault("first_sound_ms", _ms(time.perf_counter() - began))
                told.append(words)
        finally:
            # Stopped part way, the model is not left writing to nobody.
            pieces.close()
            if told:
                # Only what was said is remembered as said.
                self._history.append({"role": "assistant", "content": " ".join(told)})

    def _sound(self, words: str) -> bytes:
        """`words` said in the conversation's voice, as the bytes of a WAV file."""
        with tempfile.TemporaryDirectory(prefix="talk-") as folder:
            voice = self.parts.voice
            made = self.parts.synthesizer.say(words, Path(folder) / "said.wav", voice=voice, seed=voice.seed)
            return Path(made).read_bytes()
