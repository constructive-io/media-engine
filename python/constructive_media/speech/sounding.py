"""A word read by how it is spelled, for a word no dictionary holds.

A small speech model is handed sounds, not letters, and what turns one into
the other is a dictionary. A name, a made-up word or the name of a program
is in no dictionary, and a model handed nothing for it says nothing: the
word is left out of the sentence, and nobody is told.

    sounding.out("Brimblewick")      # 'bɹˈɪmbᵊlwɪk'

This reads such a word by the commonest way its letters are said in
English. It is rough. English spelling is not regular, a name is said the
way its owner says it, and the stress is put on the first part of the word
whatever the word is. It is there so that the word is heard and can be
understood, which is more than silence does.

The sounds are written as Kokoro's text front end writes them, which is
the International Phonetic Alphabet with one letter for each sound that
takes two: `A` for the vowel of *day*, `I` *my*, `O` *go*, `W` *now*,
`Y` *boy*.
"""

from __future__ import annotations

import re
import unicodedata

#: Every sound this writes, for an American voice and for a British one.
AMERICAN = frozenset("AIOWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹʃʊʌʒʤʧˈˌθᵊ")
BRITISH = frozenset("AIQWYabdfhijklmnpstuvwzðŋɑɒɔəɛɜɡɪɹʃʊʌʒʤʧˈˌːθᵊ")

STRESS = "ˈ"
VOWEL_LETTERS = frozenset("aeiouy")
VOWEL_SOUNDS = frozenset("AIOWYiuæɑɔəɛɜɪʊʌ")

#: A letter said by its name, as an initialism is.
NAMES = {
    "a": "ˈA", "b": "bˈi", "c": "sˈi", "d": "dˈi", "e": "ˈi", "f": "ˈɛf", "g": "ʤˈi", "h": "ˈAʧ", "i": "ˈI",
    "j": "ʤˈA", "k": "kˈA", "l": "ˈɛl", "m": "ˈɛm", "n": "ˈɛn", "o": "ˈO", "p": "pˈi", "q": "kjˈu", "r": "ˈɑɹ",
    "s": "ˈɛs", "t": "tˈi", "u": "jˈu", "v": "vˈi", "w": "dˈʌbᵊlju", "x": "ˈɛks", "y": "wˈI", "z": "zˈi",
}
DIGITS = {
    "0": "zˈɪɹO", "1": "wˈʌn", "2": "tˈu", "3": "θɹˈi", "4": "fˈɔɹ", "5": "fˈIv", "6": "sˈɪks", "7": "sˈɛvən",
    "8": "ˈAt", "9": "nˈIn",
}

#: Letters said together, the longest first. `^` only at the start of a word, `$` only at its end, `+` anywhere
#: but the start, `C` only before a consonant or the end, `R` the same but not before a second r (the a of
#: *carry* is not the a of *car*), `L` after the start and before a consonant or the end.
TOGETHER: tuple[tuple[str, str, str], ...] = (
    ("tion", "ʃən", "$"), ("sion", "ʒən", "$"), ("cian", "ʃən", "$"), ("ture", "ʧəɹ", "$"), ("ious", "iəs", "$"),
    ("eigh", "A", ""), ("augh", "ɔ", ""), ("ough", "O", ""), ("stle", "sᵊl", "L"), ("wood", "wʊd", "$"),
    ("ous", "əs", "$"), ("ing", "ɪŋ", "$"), ("ies", "iz", "$"), ("que", "k", "$"), ("are", "ɛɹ", "$"),
    ("ere", "ɪɹ", "$"), ("ble", "bᵊl", "L"), ("tle", "tᵊl", "L"), ("dle", "dᵊl", "L"), ("ple", "pᵊl", "L"),
    ("gle", "ɡᵊl", "L"), ("kle", "kᵊl", "L"), ("cle", "kᵊl", "L"), ("fle", "fᵊl", "L"), ("zle", "zᵊl", "L"),
    ("tch", "ʧ", ""), ("sch", "ʃ", "C"), ("sch", "sk", ""), ("igh", "I", ""), ("dge", "ʤ", ""), ("eau", "O", ""),
    ("air", "ɛɹ", "R"), ("ear", "ɪɹ", "R"), ("eer", "ɪɹ", "R"), ("oor", "ʊɹ", "R"), ("ore", "ɔɹ", "R"),
    ("kn", "n", "^"), ("wr", "ɹ", "^"), ("gn", "n", "^"), ("ps", "s", "^"), ("pn", "n", "^"), ("wh", "w", "^"),
    ("gh", "ɡ", "^"), ("gh", "", "+"),
    ("er", "əɹ", "$"), ("or", "əɹ", "$"), ("ay", "A", "$"), ("ey", "i", "$"), ("ow", "O", "$"), ("ew", "ju", "$"),
    ("ue", "u", "$"), ("ie", "i", "$"), ("oe", "O", "$"), ("dt", "t", "$"), ("owe", "O", "$"),
    ("ch", "ʧ", ""), ("sh", "ʃ", ""), ("th", "θ", ""), ("ph", "f", ""), ("ck", "k", ""),
    ("qu", "kw", ""), ("ee", "i", ""), ("ea", "i", ""), ("oo", "u", ""), ("ou", "W", ""), ("ow", "W", ""),
    ("oi", "Y", ""), ("oy", "Y", ""), ("ai", "A", ""), ("ay", "A", ""), ("au", "ɔ", ""), ("aw", "ɔ", ""),
    ("oa", "O", ""), ("ie", "i", ""), ("ei", "A", ""), ("ey", "A", ""), ("ue", "u", ""), ("ui", "u", ""),
    ("eu", "ju", ""), ("ew", "ju", ""),
    ("ar", "ɑɹ", "R"), ("or", "ɔɹ", "R"), ("er", "ɜɹ", "R"), ("ir", "ɜɹ", "R"), ("ur", "ɜɹ", "R"),
)
ALONE = {
    "a": "æ", "b": "b", "d": "d", "e": "ɛ", "f": "f", "h": "h", "i": "ɪ", "j": "ʤ", "k": "k", "l": "l", "m": "m",
    "o": "ɑ", "p": "p", "q": "k", "r": "ɹ", "t": "t", "u": "ʌ", "v": "v", "w": "w", "x": "ks", "z": "z",
}
#: What a vowel is before one consonant and a last, silent e: *made*, *time*, *bone*.
LONG = {"a": "A", "e": "i", "i": "I", "o": "O", "u": "u"}
SOFT = frozenset("eiy")
VOICED = frozenset("bdɡlmnɹvðzŋ")

#: An American reading as a British voice is handed it. A vowel with its r comes before the vowel alone.
TO_BRITISH = {"ɜɹ": "ɜː", "ɑɹ": "ɑː", "ɔɹ": "ɔː", "əɹ": "ə", "ɪɹ": "ɪə", "ɛɹ": "ɛː", "ʊɹ": "ʊə",
              "O": "Q", "æ": "a", "ɑ": "ɒ"}
_AMERICAN = re.compile("|".join(TO_BRITISH))

_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]")


def out(word: str, *, british: bool = False) -> str:
    """How `word` is said, going by its spelling alone. Nothing, for a word with no letter or digit in it."""
    word = word.strip()
    owned = bool(re.search(r"['’]s$", word))
    if owned:
        word = word[:-2]
    # A last e with a mark over it is said, where a bare one is not.
    word = re.sub(r"[éÉ]$", "ay", word)
    plain = "".join(letter for letter in unicodedata.normalize("NFKD", word) if letter.isascii())
    said = "".join(_part(part) for part in _PARTS.findall(plain))
    if owned and said:
        said += "z" if said[-1] in VOICED or said[-1] in VOWEL_SOUNDS else "s"
    return _british(said) if british else said


def _british(said: str) -> str:
    def here(found: re.Match[str]) -> str:
        american, after = found.group(0), said[found.end():found.end() + 1]
        # An r is said only before a vowel.
        linked = american.endswith("ɹ") and after and (after in VOWEL_SOUNDS or after == STRESS)
        return TO_BRITISH[american] + ("ɹ" if linked else "")

    return _AMERICAN.sub(here, said)


def _part(part: str) -> str:
    if part.isdigit():
        return DIGITS[part]
    letters = part.lower()
    # Capitals with no small letter after them are said letter by letter, as is anything with no vowel to say.
    if (part.isupper() and len(part) > 1) or not VOWEL_LETTERS & set(letters) or len(letters) == 1:
        return "".join(NAMES[letter] for letter in letters)
    return _stressed(_read(letters))


def _stressed(sounds: str) -> str:
    """The stress on the first vowel there is."""
    for at, sound in enumerate(sounds):
        if sound in VOWEL_SOUNDS:
            return sounds[:at] + STRESS + sounds[at:]
    return sounds


def _read(word: str) -> str:
    said: list[str] = []
    at, end = 0, len(word)
    while at < end:
        rest, letter = word[at:], word[at]
        after = word[at + 1] if at + 1 < end else ""
        # A vowel, one consonant and a silent e to end on.
        if (letter in LONG and len(rest) == 3 and rest[1] not in VOWEL_LETTERS and rest[1] not in "xwr"
                and rest[2] == "e" and at):
            said.append(LONG[letter])
            at += 1
            continue
        for letters, sound, where in TOGETHER:
            if not rest.startswith(letters):
                continue
            beyond = word[at + len(letters)] if at + len(letters) < end else ""
            open_after = bool(beyond) and beyond in VOWEL_LETTERS
            if ((where == "^" and at) or (where == "$" and beyond) or (where == "+" and not at)
                    or (where == "C" and open_after) or (where == "R" and (open_after or beyond == "r"))
                    or (where == "L" and (not at or open_after))):
                continue
            said.append(sound)
            at += len(letters)
            break
        else:
            said.append(_alone(word, at, letter, after, said))
            at += 2 if after == letter and letter not in VOWEL_LETTERS and letter != "c" else 1
    return "".join(said)


def _alone(word: str, at: int, letter: str, after: str, said: list[str]) -> str:
    """One letter, by what stands beside it."""
    last = at == len(word) - 1
    before = word[at - 1] if at else ""
    then = word[at + 2] if at + 2 < len(word) else ""
    if letter == "c":
        if after == "c" and then in SOFT:
            return "k"
        return "s" if after in SOFT and after else "k"
    if letter == "g":
        if before == "n" and (last or (after not in VOWEL_LETTERS and after not in "lr")):
            return ""                    # the g of *king*, of *kingdom*: it went into the n
        return "ʤ" if after in SOFT and after else "ɡ"
    if letter == "n":
        hard = after == "k" or (after == "g" and not (then in SOFT and then))
        return "ŋ" if hard else "n"
    if letter == "x" and not at:
        return "z"
    if letter == "s":
        if last and said and said[-1][-1:] in VOICED:
            return "z"
        return "z" if before in VOWEL_LETTERS and after in VOWEL_LETTERS and before and after else "s"
    if letter == "y":
        if not at and after in VOWEL_LETTERS and after:
            return "j"
        return "i" if last else "ɪ"
    if letter == "e" and last and any(sound in VOWEL_SOUNDS for sound in "".join(said)):
        return ""
    if last and letter in "aiou":
        return {"a": "ə", "i": "i", "o": "O", "u": "u"}[letter]
    # A u or an o with one consonant and a vowel after it is the long one: the first of *tulip*, of *modem*.
    if letter in "uo" and after and after not in VOWEL_LETTERS and then in VOWEL_LETTERS and then:
        return "u" if letter == "u" else "O"
    return ALONE[letter]


class Reader:
    """What a text front end asks for a word it does not have: the word's sounds, and how far to trust them."""

    #: Below a dictionary's word and below a program that knows the language's rules.
    RATING = 1

    def __init__(self, british: bool = False, **_: object) -> None:
        self.british = british

    def __call__(self, token: object) -> tuple[str | None, int | None]:
        said = out(str(getattr(token, "text", token)), british=self.british)
        return (said, self.RATING) if said else (None, None)
