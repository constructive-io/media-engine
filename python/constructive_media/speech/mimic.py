"""A model's own voices, mixed until they lean towards a recording.

A small speech model does not clone. It comes with some voices, each a
table of numbers, and a table that is part of one and part of another is a
voice too. This looks for the mix of them that sounds most like somebody:

    found = mimic.mimic("ada.flac", synthesizer, embedder=embedder, folder=scratch)
    synthesizer.write(found.table, "ada.safetensors", of=found.of)

It says a few sentences in each of the model's voices and asks a speaker
model how like the recording each one is. Then it starts from the nearest,
tries a mix a little way off among the nearest few, says the sentences in
that and asks again, and keeps the mix whenever it came out nearer. That
is all it is: a climb by trial, a few hundred sentences long, which a model
that says one in a tenth of a second gets through in a minute or two.

A voice is heard over several sentences and not one, because a speaker
model hears a little of what is said along with who says it, and a mix
found over one sentence is fitted to the sentence.

What comes out is not a clone. It is the nearest this model's own voices
come: the same sort of voice, at about the same pitch, and seldom one a
friend would take for the person.

It takes an adapter that can list its voices, hand one over as numbers and
write a table down to speak from: `voices`, `table` and `write`, which the
`kokoro` adapter has.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import SpeechError, Voice

#: What is said to hear a voice by: a few sentences that are not like one another.
PROBES = (
    "The quick brown fox jumps over the lazy dog, and nobody in the yard looks up.",
    "Could you tell me which of these two roads goes down to the harbour?",
    "She was watching a few young birds argue by the shore, and thought nothing of it.",
)
#: How many of the nearest voices are mixed.
OF = 6
#: How many mixes are tried.
ROUNDS = 120
#: How far off the first tries are, and how the reach shortens after so many tries that were no nearer.
STEP, SHORTER, PATIENCE = 0.35, 0.7, 20
#: A share smaller than this is not worth telling anybody about.
LEAST = 0.02
SEED = 4100


class Mimics(Protocol):
    """A synthesizer whose own voices can be mixed."""

    name: str
    model: str
    #: How the name of a file it writes a voice as ends.
    suffix: str

    def voices(self) -> list[str]:
        """The voices it has of its own, by name."""
        ...

    def table(self, voice: str) -> Any:
        """One of them as its numbers: an array, the same shape for every voice."""
        ...

    def write(self, table: Any, target: str | Path, *, of: Sequence[tuple[str, float]] = ()) -> Path:
        """Write a table down as a file whose path is a profile `say` speaks from."""
        ...

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        ...


@dataclass
class Mimicked:
    """The mix that came out nearest a recording."""

    table: Any
    #: What it is a mix of: `(voice, share)`, the largest first, shares adding up to one.
    of: list[tuple[str, float]]
    #: How like the recording it is over the probe, as the speaker model hears it.
    alike: float
    #: Every voice that was heard, the nearest first, with how like the recording it is alone.
    heard: list[tuple[str, float]] = field(default_factory=list)
    #: How many mixes were tried, and how many of them were kept.
    tried: int = 0
    kept: int = 0


def able(synthesizer: Any) -> Mimics:
    """`synthesizer`, if its voices can be mixed. A SpeechError saying what can be, if not."""
    if not (all(callable(getattr(synthesizer, part, None)) for part in ("voices", "table", "write"))
            and getattr(synthesizer, "suffix", None)):
        raise SpeechError(f"the {getattr(synthesizer, 'name', 'chosen')} speech adapter has no voices of its own to "
                          "mix: it takes one that hands a voice over as a table of numbers and speaks from one. "
                          "The kokoro adapter does: --engine kokoro")
    return synthesizer


def mimic(
    recording: str | Path, synthesizer: Mimics, *, embedder: Any, folder: str | Path, of: int = OF,
    rounds: int = ROUNDS, seed: int = SEED, probes: Sequence[str] = PROBES, among: Sequence[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Mimicked:
    """The mix of `synthesizer`'s voices that sounds most like `recording`.

    `of` is how many of the nearest voices are mixed and `rounds` how many
    mixes are tried; `among` narrows the voices to choose from. `folder` is
    somewhere to write what is said on the way, none of which is kept.
    The same `seed` finds the same mix.
    """
    import numpy as np

    from ..audio import pcm
    from ..speakers import RATE

    if of < 1 or rounds < 0:
        raise SpeechError("a mix is of one voice or more, and is tried no fewer than no times")
    if not probes:
        raise SpeechError("there is nothing to say to hear a voice by")
    names = list(among) if among is not None else synthesizer.voices()
    if not names:
        raise SpeechError(f"the speech model {getattr(synthesizer, 'model', synthesizer.name)} has no voices of "
                          "its own to mix")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tell = progress or (lambda line: None)

    def heard(sounds: Sequence[str | Path]) -> Any:
        """One voice out of some recordings of it: where they point between them."""
        rows = np.asarray(embedder.embed([pcm.read(Path(sound), rate=RATE, stereo=False) for sound in sounds]),
                          dtype=np.float64)
        middle = rows.mean(axis=0)
        return middle / (float(np.linalg.norm(middle)) or 1.0)

    wanted = heard([recording])

    def like(profile: str) -> float:
        voice = Voice("mimic", profile=profile)
        said = [synthesizer.say(probe, folder / f"probe-{at}.wav", voice=voice, seed=seed)
                for at, probe in enumerate(probes)]
        return float(np.dot(heard(said), wanted))

    ranked = sorted(((like(name), name) for name in names), key=lambda one: (-one[0], one[1]))
    basis = [name for _, name in ranked[:of]]
    tell(f"heard {len(names)} voices; the nearest is {ranked[0][1]}, {ranked[0][0]:.2f} alike")
    tables = np.stack([np.asarray(synthesizer.table(name), dtype=np.float32) for name in basis])
    weights = np.zeros(len(basis))
    weights[0] = 1.0
    best, tried, kept = ranked[0][0], 0, 0
    if len(basis) > 1 and rounds:
        chance = random.Random(seed)
        step, since = STEP, 0
        for _ in range(rounds):
            mix = np.abs(weights + np.asarray([chance.gauss(0.0, step) for _ in basis]))
            if not mix.sum():
                continue
            mix = mix / mix.sum()
            trial = synthesizer.write(np.tensordot(mix, tables, axes=1), folder / "trial.safetensors",
                                      of=list(zip(basis, mix.tolist())))
            tried += 1
            got = like(str(trial))
            if got > best:
                weights, best, since, kept = mix, got, 0, kept + 1
                tell(f"mix {tried}: {best:.2f} alike")
                continue
            since += 1
            if since % PATIENCE == 0:
                step *= SHORTER
    shares = sorted(zip(basis, weights.tolist()), key=lambda one: (-one[1], one[0]))
    return Mimicked(table=np.tensordot(weights, tables, axes=1).astype(np.float32),
                    of=[(name, share) for name, share in shares if share > 0], alike=best,
                    heard=[(name, alike) for alike, name in ranked], tried=tried, kept=kept)


def told(of: Sequence[tuple[str, float]], least: float = LEAST) -> list[tuple[str, float]]:
    """The shares of a mix worth telling somebody about: the ones that are not next to nothing."""
    return [(name, share) for name, share in of if share >= least]
