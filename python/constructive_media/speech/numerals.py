"""A number written out in English words, as it is said aloud.

A speech model is handed sounds, and what turns writing into sounds looks
words up. `1984` is not a word. Before anything can be looked up it has to
be *nineteen eighty-four*, or *one thousand, nine hundred and eighty-four*,
or *the nineteen hundred and eighty-fourth*, and which of those is for
whoever read the sentence to say.

    numerals.words(1234)                  # 'one thousand, two hundred and thirty-four'
    numerals.words(21, to="ordinal")      # 'twenty-first'
    numerals.words(1984, to="year")       # 'nineteen eighty-four'
    numerals.words(3.14)                  # 'three point one four'

The usual package for this, `num2words`, is LGPL, and the engine neither
asks for nor leans on anything that is not MIT, Apache-2.0 or BSD. Kokoro's
text front end imports that package by name. `stand_in()` is a module to
put where it would be when it is not installed: it has the one function
the front end calls, and answers for English only.
"""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
#: What each further three digits are called.
THOUSANDS = ("thousand million billion trillion quadrillion quintillion sextillion septillion octillion nonillion "
             "decillion undecillion duodecillion tredecillion quattuordecillion quindecillion sexdecillion "
             "septendecillion octodecillion novemdecillion vigintillion").split()
#: Counting words whose place in a row is not the word with `th` on it.
PLACES = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
          "twelve": "twelfth"}
ENDINGS = {1: "st", 2: "nd", 3: "rd"}
#: The name the package this stands in for is imported by.
STANDS_FOR = "num2words"
KINDS = ("cardinal", "ordinal", "ordinal_num", "year")


def words(number: int | float | str | Decimal, to: str = "cardinal") -> str:
    """`number` in words: counted (`cardinal`), placed in a row (`ordinal`, `ordinal_num`) or as a `year`."""
    if to not in KINDS:
        raise NotImplementedError(f"a number is written out as one of {', '.join(KINDS)}, not as {to!r}")
    if isinstance(number, str):
        number = Decimal(number.replace(",", "").strip())
    if isinstance(number, Decimal):
        number = int(number) if number == number.to_integral_value() else float(number)
    if isinstance(number, float):
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{number!r} is not a number that can be said")
        if not number.is_integer():
            if to != "cardinal":
                raise TypeError(f"only a whole number has a place in a row or is a year: {number!r}")
            return _fraction(number)
    whole = int(number)
    if to == "ordinal":
        return _placed(whole)
    if to == "ordinal_num":
        return f"{whole}{_ending(whole)}"
    if to == "year":
        return _year(whole)
    return _counted(whole)


def _under_hundred(number: int) -> str:
    if number < 20:
        return UNITS[number]
    tens, units = divmod(number, 10)
    return TENS[tens - 2] + (f"-{UNITS[units]}" if units else "")


def _under_thousand(number: int) -> str:
    hundreds, rest = divmod(number, 100)
    if not hundreds:
        return _under_hundred(rest)
    return f"{UNITS[hundreds]} hundred" + (f" and {_under_hundred(rest)}" if rest else "")


def _counted(number: int) -> str:
    if number < 0:
        return f"minus {_counted(-number)}"
    if number < 1000:
        return _under_thousand(number)
    groups: list[int] = []
    while number:
        number, group = divmod(number, 1000)
        groups.append(group)
    if len(groups) > len(THOUSANDS) + 1:
        # No name reaches this far, so it is read out a figure at a time.
        figures = "".join(f"{group:03d}" for group in reversed(groups)).lstrip("0")
        return " ".join(UNITS[int(figure)] for figure in figures)
    said = [f"{_under_thousand(group)} {THOUSANDS[at - 1]}" for at, group in enumerate(groups) if at and group]
    said.reverse()
    last = groups[0]
    if not last:
        return ", ".join(said)
    # 'one thousand and one', but 'one thousand, one hundred and one'.
    return ", ".join(said) + (" and " if last < 100 else ", ") + _under_thousand(last)


def _placed(number: int) -> str:
    if number < 0:
        return f"minus {_placed(-number)}"
    counted = _counted(number)
    at = max(counted.rfind(" "), counted.rfind("-")) + 1
    start, last = counted[:at], counted[at:]
    if last in PLACES:
        return start + PLACES[last]
    return start + (f"{last[:-1]}ieth" if last.endswith("y") else f"{last}th")


def _ending(number: int) -> str:
    return "th" if abs(number) % 100 in (11, 12, 13) else ENDINGS.get(abs(number) % 10, "th")


def _year(number: int) -> str:
    if number < 0:
        return f"{_year(-number)} BC"
    centuries, rest = divmod(number, 100)
    # 'two thousand and five' and 'forty-two' are counted; 'nineteen oh-five' is said in two halves.
    if not centuries or centuries >= 100 or (centuries % 10 == 0 and rest < 10):
        return _counted(number)
    start = _under_hundred(centuries)
    if not rest:
        return f"{start} hundred"
    return f"{start} oh-{UNITS[rest]}" if rest < 10 else f"{start} {_under_hundred(rest)}"


def _fraction(number: float) -> str:
    written = format(Decimal(repr(abs(number))), "f")
    whole, _, after = written.partition(".")
    said = f"{_counted(int(whole))} point {' '.join(UNITS[int(figure)] for figure in after)}"
    return f"minus {said}" if number < 0 else said


def num2words(number: Any, ordinal: bool = False, lang: str = "en", to: str = "cardinal", **_: Any) -> str:
    """`words`, asked for the way the package this stands in for is asked."""
    if lang.replace("-", "_").split("_")[0].lower() != "en":
        raise NotImplementedError(f"only English numbers are written out here; for {lang!r}, pip install {STANDS_FOR}")
    return words(number, "ordinal" if ordinal else to)


def stand_in() -> types.ModuleType:
    """A module to put where `num2words` would be: the one function, for English."""
    module = types.ModuleType(STANDS_FOR)
    module.__doc__ = "English numbers in words, from constructive-media, where num2words is not installed."
    module.num2words = num2words    # type: ignore[attr-defined]
    module.stand_in = True          # type: ignore[attr-defined]
    return module
