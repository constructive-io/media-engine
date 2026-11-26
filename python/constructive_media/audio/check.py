"""Was the line said? What a rendered line was meant to say against what is heard in it.

A line of speech is made, listened back to by a transcriber, and compared with
its words. This module is the comparison: text against text, nothing else. The
checks made on the sound itself are in `listen`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

CLEAN = "clean"
GARBLED = "garbled"
REFERENCE_BLEED = "reference_bleed"

#: A line is garbled only when it is past both of these at once.
MOST_WORD_ERROR = 0.55
LEAST_SIMILARITY = 0.55

_NOT_A_WORD = re.compile(r"[^a-z0-9 ]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


@dataclass(frozen=True)
class Verdict:
    """What a line turned out to be: fit to use, or why not."""

    ok: bool
    reason: str
    word_error: float
    similarity: float
    heard: str
    bled: str | None = None


def words(text: str | None) -> list[str]:
    """The words of a text as they are compared: lower case, no punctuation."""
    return _NOT_A_WORD.sub("", (text or "").lower()).split()


def word_error(meant: str, heard: str) -> float:
    """The share of the words meant that were heard wrong, from 0 to 1.

    A transcriber left listening to quiet after a line will sometimes go on
    writing. What is heard is cut off a little past the length of what was
    meant, so words made up after the line is over are not held against it.
    """
    wanted = words(meant)
    if not wanted:
        return 0.0
    return min(1.0, _distance(wanted, _as_long_as(wanted, words(heard))) / len(wanted))


def similarity(meant: str, heard: str) -> float:
    """How alike the two are letter by letter, from 0 to 1.

    A word heard slightly wrong is a whole wrong word to `word_error` and
    nearly right here.
    """
    wanted = words(meant)
    if not wanted:
        return 1.0
    return SequenceMatcher(None, " ".join(wanted), " ".join(_as_long_as(wanted, words(heard)))).ratio()


def bleed(meant: str, heard: str, reference: str, *, last: int = 4) -> str | None:
    """The words heard before the line that came from the end of the voice's reference.

    A voice cloned from a recording will sometimes say the last words of that
    recording before the line it was given. One word more on a line of ten is
    a word error of a tenth, far too little to notice by counting; what gives
    it away is where it is. So this looks only at what is heard before the
    line's first word, and only against the last words of the reference.

    A transcriber hears the same stray word differently from take to take, so
    a long word matches when it is part of a reference word, holds one, or is
    half like it. A short word must be the reference's last word exactly.
    """
    wanted, got = words(meant), words(heard)
    ending = words(reference)[-last:]
    if not wanted or not got or not ending:
        return None
    before = []
    for word in got[:3]:
        if word == wanted[0]:
            break
        before.append(word)
    else:
        # The line never begins: that is a garble, not a bleed.
        return None
    for word in before:
        if len(word) < 4:
            if word == ending[-1]:
                return " ".join(before)
            continue
        for other in ending:
            if word in other or other in word or SequenceMatcher(None, word, other).ratio() >= 0.5:
                return " ".join(before)
    return None


def check(meant: str, heard: str, *, reference: str = "", most_word_error: float = MOST_WORD_ERROR,
          least_similarity: float = LEAST_SIMILARITY) -> Verdict:
    """Judge a line by what was heard in it.

    It is garbled only when the words are very wrong and the letters are
    unlike as well. Either alone is the transcriber: a short line with a
    made-up tail scores badly on words and well on letters. Calling a good
    line bad costs one more try; calling a bad one good is heard by everyone.

    Given the words of the voice's reference recording, a line that begins
    with the end of them fails too.
    """
    error, alike = word_error(meant, heard), similarity(meant, heard)
    if error > most_word_error and alike < least_similarity:
        return Verdict(False, GARBLED, error, alike, heard)
    bled = bleed(meant, heard, reference)
    if bled:
        return Verdict(False, REFERENCE_BLEED, error, alike, heard, bled)
    return Verdict(True, CLEAN, error, alike, heard)


def calm(text: str) -> tuple[str, bool]:
    """A text with its shouted words in ordinary capitals, and whether anything changed.

    Speech models stumble over words in capitals: they spell them out or
    stutter on them. A word of four letters or more in capitals, or one of a
    run of them, becomes a capitalised word. A short one alone is left, since
    it is more likely an abbreviation that is meant to be spelled out.
    """
    found = _WORD.findall(text)
    loud = [_shouted(word) for word in found]
    mend = {
        n for n, word in enumerate(found)
        if loud[n] and (len(word.replace("'", "")) >= 4
                        or (n > 0 and loud[n - 1]) or (n < len(found) - 1 and loud[n + 1]))
    }
    if not mend:
        return text, False
    count = iter(range(len(found)))
    return _WORD.sub(lambda match: match.group(0).capitalize() if next(count) in mend else match.group(0),
                     text), True


def _shouted(word: str) -> bool:
    letters = word.replace("'", "")
    return letters.isupper() and len(letters) >= 2


def _as_long_as(wanted: list[str], got: list[str]) -> list[str]:
    return got[:int(len(wanted) * 1.3) + 3]


def _distance(wanted: list[str], got: list[str]) -> int:
    """How many words must be changed, added or dropped to turn one into the other."""
    row = list(range(len(got) + 1))
    for n, word in enumerate(wanted, 1):
        new = [n]
        for m, other in enumerate(got, 1):
            new.append(min(row[m] + 1, new[-1] + 1, row[m - 1] + (word != other)))
        row = new
    return row[-1]
