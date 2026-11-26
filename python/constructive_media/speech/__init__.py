"""Speech: words said aloud in somebody's voice.

A `Synthesizer` is given words and a `Voice` and writes a recording of them
being said. The engine asks for one by slot, `speech`, and never knows which
model or which program spoke.

A voice belongs to a persona and has a way of speaking, its emotion: one
persona may have a neutral voice, a warm one and a whispered one. A line asks
for a persona and an emotion; when the persona has no voice for that emotion
its neutral voice speaks, and failing that the first voice it has.

No voice is kept here. A voice is a row in the database that says which
adapter speaks it and what that adapter knows it by.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import adapters, db
from ..settings import Settings

NEUTRAL = "neutral"
#: A voice in one of these states may speak. One being trained or known to be
#: broken may not.
USABLE = ("ready", "provisional")


class SpeechError(RuntimeError):
    """Words could not be said, or there is no voice to say them in."""


@dataclass(frozen=True)
class Voice:
    """One way a persona speaks.

    `engine` is the name of the adapter that speaks it, or None for the
    configured one. `profile` is what that adapter knows the voice by. A
    voice cloned from a recording also has that recording and the words said
    in it. `seed` pins the voice to one rendering of itself; without it each
    line is given a seed of its own.
    """

    persona: str
    emotion: str = NEUTRAL
    engine: str | None = None
    profile: str | None = None
    reference_audio: str | None = None
    reference_text: str | None = None
    seed: int | None = None


@runtime_checkable
class Synthesizer(Protocol):
    """Something that says words aloud."""

    name: str
    #: What is recorded beside what it said, so renderings can be told apart.
    model: str

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        """Write `text`, said in `voice`, to `target` as a WAV file.

        The same text, voice and seed should give the same recording; another
        seed, another reading of it. That is how a line that came out wrong
        is said again.
        """
        ...


def synthesizer(name: str | None = None, *, settings: Settings | None = None) -> Synthesizer:
    """The chosen synthesizer, or the configured default."""
    return adapters.get("speech", name, settings=settings)


@runtime_checkable
class Voices(Protocol):
    """Somewhere voices are kept."""

    def find(self, persona: str, emotion: str | None = None) -> Voice:
        """The voice a persona says a line in. A SpeechError when it has none."""
        ...

    def emotions(self) -> dict[str, set[str]]:
        """Every persona that has a voice, and the emotions it has one for."""
        ...


def pick(voices: Sequence[Voice], emotion: str | None = None) -> Voice | None:
    """Of one persona's voices: the one for `emotion`, else the neutral one, else the first."""
    for wanted in (emotion, NEUTRAL):
        for voice in voices:
            if wanted and voice.emotion == wanted:
                return voice
    return min(voices, key=lambda voice: voice.emotion) if voices else None


class GivenVoices:
    """Voices handed over as they are, for a program that keeps its own."""

    def __init__(self, voices: Iterable[Voice]) -> None:
        self._voices: dict[str, list[Voice]] = {}
        for voice in voices:
            self._voices.setdefault(voice.persona, []).append(voice)

    def find(self, persona: str, emotion: str | None = None) -> Voice:
        return _found(pick(self._voices.get(persona, ()), emotion), persona)

    def emotions(self) -> dict[str, set[str]]:
        return {persona: {voice.emotion for voice in voices} for persona, voices in self._voices.items()}


_SELECT = """
SELECT p.slug, v.emotion, v.engine, v.profile_ref, v.reference_audio_path, v.reference_text, v.default_seed
  FROM media.voices v JOIN media.personas p ON p.id = v.persona_id
 WHERE v.direction = 'synthesis' AND v.status IN ({usable})
""".format(usable=", ".join(f"'{state}'" for state in USABLE))


class StoredVoices:
    """The voices in the database."""

    def find(self, persona: str, emotion: str | None = None) -> Voice:
        cur = db.cursor()
        # The newest first: a voice made again takes the place of the one before.
        cur.execute(_SELECT + " AND p.slug = %s ORDER BY v.created_at DESC, v.id", (persona,))
        return _found(pick([Voice(*row) for row in cur.fetchall()], emotion), persona)

    def emotions(self) -> dict[str, set[str]]:
        cur = db.cursor()
        cur.execute(_SELECT)
        have: dict[str, set[str]] = {}
        for row in cur.fetchall():
            have.setdefault(row[0], set()).add(row[1])
        return have


def _found(voice: Voice | None, persona: str) -> Voice:
    if voice is None:
        raise SpeechError(f"{persona!r} has no voice to speak in")
    return voice


@dataclass(frozen=True)
class Kept:
    """A voice as it is kept: the voice, and whether it may speak."""

    voice: Voice
    status: str


def keep(voice: Voice, *, new: bool = False, status: str = "ready") -> bool:
    """Write a voice down, in place of the one its persona had for that emotion.

    True when there was one to take the place of. With `new`, a persona that
    is not there yet is added.
    """
    from ..speakers.names import person

    if not voice.profile:
        raise SpeechError("a voice needs a profile: what its adapter knows it by")
    engine = voice.engine or adapters.registry().describe("speech", settings=Settings.from_env()).name
    with db.transaction():
        persona_id = person(voice.persona, new=new)
        cur = db.cursor()
        cur.execute("DELETE FROM media.voices WHERE persona_id = %s AND direction = 'synthesis' AND emotion = %s",
                    (persona_id, voice.emotion))
        replaced = cur.rowcount > 0
        cur.execute(
            "INSERT INTO media.voices (persona_id, direction, engine, emotion, profile_ref, reference_audio_path, "
            "reference_text, default_seed, status) VALUES (%s, 'synthesis', %s, %s, %s, %s, %s, %s, %s)",
            (persona_id, engine, voice.emotion, voice.profile, voice.reference_audio, voice.reference_text,
             voice.seed, status),
        )
    return replaced


def drop(persona: str, emotion: str | None = None) -> int:
    """Forget a persona's voice for one emotion, or all of its voices. How many went."""
    cur = db.cursor()
    cur.execute(
        "DELETE FROM media.voices v USING media.personas p WHERE p.id = v.persona_id AND p.slug = %s "
        "AND v.direction = 'synthesis'" + (" AND v.emotion = %s" if emotion else ""),
        (persona, emotion) if emotion else (persona,),
    )
    return cur.rowcount


def kept() -> list[Kept]:
    """Every voice written down, by persona and emotion."""
    cur = db.cursor()
    cur.execute(
        "SELECT p.slug, v.emotion, v.engine, v.profile_ref, v.reference_audio_path, v.reference_text, "
        "v.default_seed, v.status FROM media.voices v JOIN media.personas p ON p.id = v.persona_id "
        "WHERE v.direction = 'synthesis' ORDER BY p.slug, v.emotion, v.created_at DESC"
    )
    return [Kept(Voice(*row[:7]), row[7]) for row in cur.fetchall()]
