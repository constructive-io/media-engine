"""A voice between other voices.

A model that clones hears a recording as a row of numbers before it says
anything in that voice. Two voices are two rows, and a row part of the way
from one to the other is a voice nobody has: nearer the first the more of
the first is asked for.

    mixed = blend.mix([("ada.flac", 0.35), ("grace.flac", 0.65)], synthesizer)
    blend.Blended(synthesizer, mixed).say("The lantern is lit.", "line.wav", voice=voice)

The way between two rows is taken round the arc that joins them, not along
the straight line. The rows of a model like this lie far from the middle,
at about one distance from it; a straight line between two of them cuts
inside, and a model given a row from in there loses its place and talks on.
The arc stays at the distance the voices are at.

It takes an adapter that can hand a voice over as its numbers and say a
line from numbers: `embed` and `say_as`, which the `mlx` adapter has.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import SpeechError, Voice

#: Two rows closer than this, in radians, are one voice.
SAME = 1e-9
#: The most times the meeting point of several voices is looked for. It is found in a few.
ROUNDS = 200


@runtime_checkable
class Blends(Protocol):
    """A synthesizer that can hand a voice over as numbers, and say a line from numbers."""

    name: str
    model: str

    def embed(self, recording: str | Path) -> list[float]:
        """A voice as the numbers this model hears it by, from a recording of it."""
        ...

    def say_as(self, text: str, target: Path, *, embedding: list[float], seed: int | None = None) -> Path:
        """Say `text` in the voice that `embedding` is."""
        ...


def part(written: str) -> tuple[str, float]:
    """One voice of a blend as it is written: `ada:0.35`, or `ada` for a whole share.

    A share is a number that is not negative. Shares are in proportion, so
    `ada:1 grace:2` is a third of one and two thirds of the other.
    """
    name, colon, share = written.rpartition(":")
    if colon:
        try:
            amount = float(share)
        except ValueError:
            amount = None
        if amount is not None and name:
            if not math.isfinite(amount) or amount < 0:
                raise SpeechError(f"{written}: a share is a number from 0 up, as in ada:0.35")
            return name, amount
    if not written.strip():
        raise SpeechError("a voice to blend has a name")
    return written, 1.0


def shares(weights: Sequence[float]) -> list[float]:
    """Weights as shares of one whole."""
    if any(not math.isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("a share is a number from 0 up")
    whole = float(sum(weights))
    if whole <= 0:
        raise ValueError("a blend with no share of any voice is no voice")
    return [weight / whole for weight in weights]


def _length(row: Sequence[float]) -> float:
    return math.sqrt(math.fsum(one * one for one in row))


def slerp(rows: Sequence[Sequence[float]], weights: Sequence[float]) -> list[float]:
    """The voice that is `weights` of each of `rows`: one row of numbers for each voice.

    Two voices meet on the arc between them, as far along it as their
    shares say. More than two meet at the one point on the sphere that is
    nearest them all by their shares, whatever order they were given in. The
    answer is as long as the voices are, taken by their shares.
    """
    if not rows or len(rows) != len(weights):
        raise ValueError("a blend needs a share for every voice, and a voice for every share")
    if len({len(row) for row in rows}) != 1 or not len(rows[0]):
        raise ValueError("voices can be blended only as one model hears them: these rows are not the same size")
    part_of = shares(weights)
    lengths = [_length(row) for row in rows]
    if any(share and not length for share, length in zip(part_of, lengths)):
        raise ValueError("a voice heard as nothing cannot be blended")
    units = [([float(value) / length for value in row], share)
             for row, length, share in zip(rows, lengths, part_of) if share]
    # From the middle of them as the crow flies, or from the largest when that is nowhere.
    held = _unit([math.fsum(share * unit[n] for unit, share in units) for n in range(len(rows[0]))])
    if held is None:
        held = max(units, key=lambda one: one[1])[0]
    for _ in range(ROUNDS):
        pull = [0.0] * len(held)
        for unit, share in units:
            cosine = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(held, unit))))
            angle = math.acos(cosine)
            if angle < SAME:
                continue
            sine = math.sin(angle)
            if sine < SAME:
                raise ValueError("two voices that are opposites have nothing between them")
            # Which way this voice lies from here, and how far, along the sphere.
            towards = share * angle / sine
            pull = [so_far + towards * (value - cosine * here) for so_far, value, here in zip(pull, unit, held)]
        far = _length(pull)
        if far < SAME:
            break
        near, along = math.cos(far), math.sin(far) / far
        held = _unit([near * here + along * step for here, step in zip(held, pull)]) or held
    reach = math.fsum(share * length for share, length in zip(part_of, lengths))
    return [reach * value for value in held]


def _unit(row: Sequence[float]) -> list[float] | None:
    """A row at length one, or None for a row with no length."""
    length = _length(row)
    return [value / length for value in row] if length > SAME else None


def able(synthesizer: Any) -> Blends:
    """`synthesizer`, if it can blend. A SpeechError saying what can, if it cannot."""
    if not (callable(getattr(synthesizer, "embed", None)) and callable(getattr(synthesizer, "say_as", None))):
        raise SpeechError(f"the {getattr(synthesizer, 'name', 'chosen')} speech adapter cannot blend voices: it "
                          "takes one that hands a voice over as numbers and says a line from numbers. The mlx "
                          "adapter does: CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx, or --engine mlx")
    return synthesizer


def mix(sources: Sequence[tuple[str | Path, float]], synthesizer: Blends) -> list[float]:
    """The voice that is so much of each recording: `(recording, share)` for each."""
    try:
        part_of = shares([share for _, share in sources])
    except ValueError as error:
        raise SpeechError(str(error)) from None
    if sum(1 for share in part_of if share) < 2:
        raise SpeechError("a blend is of two voices or more, each with a share")
    rows = [synthesizer.embed(recording) for recording, _ in sources]
    try:
        return slerp(rows, part_of)
    except ValueError as error:
        raise SpeechError(str(error)) from None


class Blended:
    """A synthesizer that says every line in one blended voice, whichever voice is asked for."""

    def __init__(self, synthesizer: Blends, embedding: Sequence[float]) -> None:
        self.name = synthesizer.name
        self.model = getattr(synthesizer, "model", synthesizer.name)
        self._synthesizer = synthesizer
        self._embedding = [float(value) for value in embedding]

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        return self._synthesizer.say_as(text, Path(target), embedding=self._embedding, seed=seed)
