"""Editing: from everything that was said to the parts worth keeping.

A transcript is hours of speech, most of it of no use to anyone. This package
narrows it down, and every step leaves what it found in the database, where
the next step, or a person, can read it:

- `said`      a take's speech on the take's own timeline, however many
              microphones heard it
- `passages`  stretches of that speech that stand on their own
- `quality`   whether a passage is worth hearing, from its words alone
- `flags`     places to cut, to look at, or to stay away from
- `scores`    every measurement of a passage or a shot in one ledger, and the
              recipes that weigh them into a ranking
- `decisions` what a person ruled: keep this, never that, this one over that
- `ranker`    what a person prefers, learnt from the choices they made
- `script`    the ranked passages as a document to mark up and hand back
- `select`    shots picked to be good and unalike, then put in an order
- `timelines` a cut, kept as rows, with what was said about each clip
- `assemble`  an interview cut from its passages, each with a camera that
              was rolling and the microphone that heard it best
- `render`    a cut made into a file to watch, its picture and sound in step
              and its sound unbroken wherever the recording carried on

Nothing here runs a model. It all works from what transcription, sync and the
video analysis have already stored, so it is cheap enough to run over
everything before anything expensive is allowed near it. Only `render` reads
the recordings themselves, and it does that through `ffmpeg`.

The word lists (fillers, hedges, words a sentence cannot end on) are English.
Speech in another language still gets every measure that does not lean on them.
"""

from __future__ import annotations


class EditError(LookupError):
    """Something an edit was asked to work from is not there."""


def clock(seconds: float) -> str:
    """A time as minutes and seconds: 754.25 is `12:34.2`."""
    minutes, rest = divmod(round(abs(seconds), 1), 60)
    return f"{'-' if seconds < 0 else ''}{int(minutes)}:{rest:04.1f}"
