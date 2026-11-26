"""The markers inside one line of dialogue.

    ADA: calm about it <break 0.5> and then it hit me.
    ADA: I hear you, {excited: but this changes everything!}
    ADA: {whisper, reverb:cathedral: come here} <sfx DOOR> {firm: we need to talk}.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .values import number

# What comes before a span's text is a list of `key` or `key:value`, ended by one
# colon. It is matched a piece at a time: `reverb:cathedral` is itself a key and
# a value, so "everything up to the first colon" would cut it in half.
_PIECE = r"[a-zA-Z_][A-Za-z0-9_.-]*(?::[a-zA-Z0-9_.+-]+)?"
_BEFORE = rf"(?:{_PIECE}\s*,\s*)*{_PIECE}"
_MARKER = re.compile(
    r"<break\s+([0-9.]+)>|<([0-9.]+)>"
    r"|<sfx\s+([A-Z][A-Z0-9_-]*)(?:\s+([^>]*))?>"
    rf"|\{{({_BEFORE}):([^{{}}]*)\}}"
)


@dataclass(frozen=True)
class Piece:
    """One piece of a line, before its settings are checked."""
    type: str                       # text, break or sfx
    text: str = ""
    seconds: int | float | None = None
    sound: str = ""
    # The settings as written: a span's (`whisper, reverb:hall`) or a sound's.
    raw: str = ""


def pieces(text: str) -> list[Piece]:
    """A line's words as the pieces between and inside its markers, in order.

    A break whose seconds cannot be read comes back with none, and `raw` holding
    the marker, for whoever asked to report.
    """
    found: list[Piece] = []
    at, any_marker = 0, False
    for match in _MARKER.finditer(text):
        any_marker = True
        before = text[at:match.start()].strip()
        if before:
            found.append(Piece("text", text=before))
        long, short, sound, playing, settings, words = match.groups()
        if long is not None or short is not None:
            found.append(Piece("break", seconds=number(long if long is not None else short), raw=match[0]))
        elif sound is not None:
            found.append(Piece("sfx", sound=sound, raw=playing or ""))
        elif words.strip():
            found.append(Piece("text", text=words.strip(), raw=settings))
        at = match.end()
    if not any_marker:
        return [Piece("text", text=text)]
    rest = text[at:].strip()
    if rest:
        found.append(Piece("text", text=rest))
    return _folded(found) or [Piece("text", text=text)]


def _spoken(text: str) -> bool:
    return any(unicodedata.category(letter)[0] in "LN" for letter in text)


def _folded(found: list[Piece]) -> list[Piece]:
    """Punctuation left on its own beside a span, such as the "." in `{firm: talk}.`,
    joins the words before it. Nobody says a full stop."""
    kept: list[Piece] = []
    for piece in found:
        if piece.type == "text" and kept and kept[-1].type == "text" and not _spoken(piece.text):
            last = kept[-1]
            kept[-1] = Piece("text", text=last.text + piece.text, raw=last.raw)
        else:
            kept.append(piece)
    return kept
