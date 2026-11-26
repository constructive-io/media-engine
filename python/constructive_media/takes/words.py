"""What each recording says, as the evidence two recordings share.

Two microphones in one room hear the same sentences and little else in common:
one is on a collar, the other across the room, at different levels, with
different noise. Their transcripts need not be right, only wrong in the same
way, and a recogniser mishears a phrase the same way on both often enough.

A word here is lower-case letters and apostrophes with its start time, in the
recording's own seconds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .. import db

#: One spoken word and when it starts.
Spoken = tuple[str, float]

#: Words that turn up in any stretch of English, and so say nothing about
#: which stretch this is. Other languages lean on the rule in `align` that
#: ignores whatever is said often.
STOP = frozenset("""
    a about all an and are as at be been but can could did do does for from had
    has have he her here him his how i if in is it its just know like me my no
    not of on or our out she should so than that the their them then there
    these they think this those to up us was we were what when where which who
    why will with would yes you your
""".split())


def normalise(word: str) -> str:
    """A word as it is compared: letters and apostrophes, case folded."""
    return "".join(c for c in word.casefold() if c.isalpha() or c == "'")


def spoken(asset_ids: Iterable[Any]) -> dict[Any, list[Spoken]]:
    """Every word of each asset's transcript, in order, read in one query."""
    wanted = [str(asset_id) for asset_id in asset_ids]
    found: dict[Any, list[Spoken]] = {}
    if not wanted:
        return found
    cur = db.cursor()
    cur.execute(
        """SELECT s.asset_id, w.word, w.start_s
             FROM media_video.words w
             JOIN media_video.segments s ON s.id = w.segment_id
            WHERE s.asset_id = ANY(%s::uuid[]) AND w.start_s IS NOT NULL
            ORDER BY s.asset_id, w.start_s, w.idx""",
        (wanted,),
    )
    for asset_id, word, start in cur.fetchall():
        if text := normalise(word):
            found.setdefault(asset_id, []).append((text, float(start)))
    return found


def telling(words: Sequence[Spoken], min_letters: int = 3) -> list[Spoken]:
    """The words that carry evidence: long enough, and not in `STOP`."""
    return [(word, start) for word, start in words
            if len(word) >= min_letters and word not in STOP]
