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

Nothing here runs a model. It all works from what transcription, sync and the
video analysis have already stored, so it is cheap enough to run over
everything before anything expensive is allowed near it.

The word lists (fillers, hedges, words a sentence cannot end on) are English.
Speech in another language still gets every measure that does not lean on them.
"""

from __future__ import annotations


class EditError(LookupError):
    """Something an edit was asked to work from is not there."""
