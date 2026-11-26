"""One line, said until it is said right.

A speech model does not always say what it was given. It drops a word, says
one twice, runs on, or says something else altogether, and which of these it
does changes with the seed. So a line is said, listened back to, checked, and
when the check fails said again with another seed. A line is only called
good when a take of it passed; when none did, the best one is kept and the
line is marked for a person to hear.

What is tried, in order:

1. The line as written, then with its shouted words calmed, then more seeds.
2. For a short line, or when every failure was the voice's and not the
   words', more seeds still. Such a line is never reworded: on three words
   one misheard word looks like a garble, and no rewording cures a voice.
3. Only when asked: the line reworded by a text model and said again. This
   changes what the script says, so it is off unless turned on.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .. import effects as effects_slot
from ..effects import Effects
from ..ffmpeg import FfmpegError
from ..settings import Settings
from ..speech import SpeechError, Synthesizer, Voice, Voices
from ..text import TextError, Writer
from ..transcription import Transcriber, TranscriptionError
from . import check, listen, pcm

CLEAN = "clean"
HEALED = "healed"
REWORDED = "reworded"
NEEDS_REVIEW = "needs_review"
#: From best to worst. A line of several pieces is as good as its worst piece.
STATUSES = (CLEAN, HEALED, REWORDED, NEEDS_REVIEW)

STUTTER = "stutter"
CUT_OFF = "cut_off"
ERROR = "error"
#: Failures that are the voice's doing. Another seed mends them; other words do not.
OF_THE_VOICE = frozenset({check.REFERENCE_BLEED, STUTTER, CUT_OFF})

ATTEMPTS = 5
MORE_SEEDS = 6
REWORDINGS = 3
#: A line of this many words or fewer is never reworded.
SHORT = 3
#: The rate a piece of quiet is made at when a line has nothing said in it.
QUIET_RATE = 24_000

_SMALLEST = 256

_REWORD = (
    "Rephrase this sentence so that a text-to-speech voice reads it cleanly. Do not change what it means "
    "or the tone it is said in. Keep it natural spoken English. Answer with the rephrased sentence only: "
    "no quotation marks and nothing before or after it.\n\nSentence: {text}\nRephrased:"
)
_LABEL = re.compile(r"^(rephrased|sentence)\s*:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Take:
    """One reading of a line, and what was made of it.

    `path` is None when nothing was made. `spoken` is the text that was given
    to the voice, which is the line unless it was calmed or reworded.
    `attempts` is how many readings had been made when this one was kept.
    `repeat` is the clearest stutter heard in it, `tail_db` how loud it ended
    when it ended too loud to have ended.
    """

    path: Path | None
    seed: int
    spoken: str
    verdict: check.Verdict
    status: str
    attempts: int
    repeat: listen.Repeat | None = None
    tail_db: float | None = None

    @property
    def ok(self) -> bool:
        return self.verdict.ok


def speak(
    text: str, voice: Voice, folder: str | Path, *, synthesizer: Synthesizer, transcriber: Transcriber, seed: int,
    name: str = "line", attempts: int = ATTEMPTS, more_seeds: int = MORE_SEEDS, short: int = SHORT,
    reword: Callable[[str], str] | None = None, rewordings: int = REWORDINGS, repeats: bool = True,
    language: str = "en",
) -> Take:
    """Say `text` in `voice` until a take passes, and return that take.

    Takes are written to `folder` as `<name>_a<n>.wav`. A voice with a seed of
    its own starts from that; otherwise from `seed`. With `repeats` off a
    take is not listened to for stutters. `reword` is given a text and
    returns another way of saying it.
    """
    folder = Path(folder)
    calmed, changed = check.calm(text)
    first = voice.seed if voice.seed is not None else seed
    best: Take | None = None
    made = 0
    reasons: set[str] = set()

    def attempt(spoken: str, with_seed: int, status: str) -> Take | None:
        nonlocal best, made
        made += 1
        take = _take(spoken, voice, folder / f"{name}_a{made}.wav", with_seed, status, made,
                     synthesizer=synthesizer, transcriber=transcriber, repeats=repeats, language=language)
        if not take.ok:
            reasons.add(take.verdict.reason)
        if best is None or take.verdict.word_error <= best.verdict.word_error:
            best = take
        return take if take.ok else None

    plan = [(text, first)] + ([(calmed, first)] if changed else [])
    plan += [(calmed, first + n * 257) for n in range(1, attempts)]
    for n, (spoken, with_seed) in enumerate(plan[:attempts]):
        if take := attempt(spoken, with_seed, HEALED if n else CLEAN):
            return take

    only_seeds = len(text.split()) <= short or (bool(reasons) and reasons <= OF_THE_VOICE)
    if only_seeds:
        for n in range(more_seeds):
            if take := attempt(calmed, first + 3000 + n * 401, HEALED):
                return take
    elif reword is not None and reasons != {ERROR}:
        # A line nothing could say at all is not a line the words are wrong in.
        current = calmed
        for n in range(rewordings):
            current = reword(current)
            for with_seed in (first + 7000 + n * 131, first + 7131 + n * 131):
                if take := attempt(current, with_seed, REWORDED):
                    return take

    assert best is not None
    return replace(best, status=NEEDS_REVIEW, attempts=made)


def _take(spoken: str, voice: Voice, path: Path, seed: int, status: str, made: int, *, synthesizer: Synthesizer,
          transcriber: Transcriber, repeats: bool, language: str) -> Take:
    """Say it once, clean its start, listen back and judge."""
    repeat, tail_db = None, None
    try:
        try:
            synthesizer.say(spoken, path, voice=voice, seed=seed)
        except Exception as error:  # noqa: BLE001 - whatever a model does, a long script goes on past one line
            raise SpeechError(_said(error)) from error
        if not path.is_file() or path.stat().st_size < _SMALLEST:
            raise SpeechError("nothing was said")
        rate = pcm.rate(path)
        # Once, here, and never again on the same take.
        sound = listen.clean_start(pcm.read(path, stereo=False), rate)
        pcm.write(path, sound, rate)
        try:
            heard = " ".join(segment.text.strip() for segment in transcriber.transcribe(path, language=language))
        except Exception as error:  # noqa: BLE001 - as above
            raise TranscriptionError(_said(error)) from error
        verdict = check.check(spoken, heard, reference=voice.reference_text or "")
        if verdict.ok and repeats:
            try:
                repeat = listen.stutter(pcm.read(path, rate=listen.LISTENING_RATE, stereo=False))
            except (FfmpegError, ValueError):
                repeat = None  # a check that could not be made is not a failed line
            if repeat is not None:
                verdict = replace(verdict, ok=False, reason=STUTTER)
        if verdict.ok:
            stopped, level = listen.cut_off(sound, rate)
            if stopped:
                verdict, tail_db = replace(verdict, ok=False, reason=CUT_OFF), level
    except (SpeechError, TranscriptionError, FfmpegError, OSError) as error:
        verdict = check.Verdict(False, ERROR, 1.0, 0.0, _said(error))
        return Take(path if path.is_file() and path.stat().st_size >= _SMALLEST else None,
                    seed, spoken, verdict, status, made)
    return Take(path, seed, spoken, verdict, status, made, repeat, tail_db)


def _said(error: Exception) -> str:
    """An error in a line: what kind when it has nothing to say for itself."""
    return " ".join(str(error).split())[:300] or type(error).__name__


def rewording(writer: Writer) -> Callable[[str], str]:
    """A function that rewords a line with a text model, for `speak`.

    When the model cannot be reached or answers nothing, the line comes back
    as it was.
    """

    def reword(text: str) -> str:
        try:
            written = writer.write(_REWORD.format(text=text), limit=200)
        except TextError:
            return text
        lines = [line.strip() for line in written.splitlines() if line.strip()]
        return (_LABEL.sub("", lines[-1]).strip().strip('"').strip() if lines else "") or text

    return reword


@dataclass(frozen=True)
class Line:
    """A whole line as it was said: its take, the voice, the words, and the
    effects that were put on pieces of it."""

    take: Take
    voice: Voice
    text: str
    effects: tuple[dict[str, Any], ...] = ()


def perform(
    segments: Sequence[dict[str, Any]], folder: str | Path, *, persona: str, voices: Voices,
    synthesizer: Callable[[str | None], Synthesizer], transcriber: Transcriber, seed: int, name: str = "line",
    emotion: str | None = None, line_seed: int | None = None, backend: str | None = None,
    settings: Settings | None = None, **how: Any,
) -> Line:
    """Say a line of a script, which may be several pieces said one after another.

    `segments` are the line's pieces as a script is read: text, which may
    ask for an emotion, a seed or effects of its own; breaks, which are
    quiet; and sounds, which are not said and are passed over. `synthesizer`
    is given the name of a voice's engine and returns what speaks it.
    `line_seed` pins every piece to one seed unless the piece has its own.
    `how` is passed on to `speak`.

    A line of one plain piece is that piece. Otherwise each piece is said
    and checked by itself, and they are joined. The whole is as good as its
    worst piece, and it is that piece's verdict the whole is given.
    """
    folder = Path(folder)
    said = [piece for piece in segments if piece["type"] == "text"]
    if len(said) == 1 and len(segments) == 1 and not said[0].get("overrides"):
        voice = _pinned(voices.find(persona, emotion), line_seed)
        take = speak(said[0]["text"], voice, folder, synthesizer=synthesizer(voice.engine), transcriber=transcriber,
                     seed=seed, name=name, **how)
        return Line(take, voice, said[0]["text"])

    pieces: list[tuple[str, Any]] = []
    takes: list[Take] = []
    applied: list[dict[str, Any]] = []
    voice, rate = None, None
    for piece in segments:
        if piece["type"] == "break":
            pieces.append(("break", float(piece["seconds"])))
            continue
        if piece["type"] != "text":
            continue
        asked = piece.get("overrides") or {}
        voice = _pinned(voices.find(persona, asked.get("emotion", emotion)), asked.get("seed", line_seed))
        # Each piece has takes of its own, so each has a name of its own.
        take = speak(piece["text"], voice, folder, synthesizer=synthesizer(voice.engine), transcriber=transcriber,
                     seed=seed + len(takes) * 7, name=f"{name}_s{len(takes)}", **how)
        takes.append(take)
        if take.path is None:
            continue
        path = take.path
        wanted = Effects.from_overrides(asked)
        if wanted and take.status != NEEDS_REVIEW:
            path = effects_slot.apply(path, folder / f"{name}_s{len(takes) - 1}_fx.wav", wanted,
                                      backend=backend, settings=settings)
            applied.append({key: value for key, value in asked.items() if key not in ("emotion", "seed")})
        rate = rate or pcm.rate(path)
        pieces.append((listen.TEXT, pcm.read(path, rate=rate, stereo=False)))

    if not takes or voice is None:
        raise SpeechError("a line with nothing to say in it cannot be said")
    import numpy as np

    rate = rate or QUIET_RATE
    sound = [(kind, np.zeros(int(round(what * rate)), dtype=np.float32) if kind == "break" else what)
             for kind, what in pieces]
    # The whole is judged by its worst piece, and the first of those when several are as bad.
    worst = max(takes, key=lambda take: STATUSES.index(take.status))
    whole = Take(
        pcm.write(folder / f"{name}.wav", listen.join(sound, rate), rate) if any(t.path for t in takes) else None,
        worst.seed, " ".join(take.spoken for take in takes), worst.verdict, worst.status,
        sum(take.attempts for take in takes), worst.repeat, worst.tail_db,
    )
    return Line(whole, voice, " ".join(piece["text"] for piece in said), tuple(applied))


def _pinned(voice: Voice, seed: int | None) -> Voice:
    return voice if seed is None else replace(voice, seed=int(seed))
