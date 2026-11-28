"""Speech on this machine from a small, quick model with voices of its own.

[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0) is a speech
model of 82 million weights. On Apple silicon it says a sentence in about a
tenth of a second, which is quick enough to talk with. It does not clone:
it comes with a few dozen voices, each a table of numbers, and it speaks in
one of those, or in a table that is a mix of them (`media mimic` makes one
that leans towards a recording).

    pip install "constructive-media[audio]" mlx-audio misaki spacy
    python -m spacy download en_core_web_sm
    CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=kokoro

None of those is one of the engine's extras, and the engine installs none
of them. mlx-audio (MIT) runs the model. misaki (Apache-2.0) turns English
into the sounds the model is handed, and for that it needs spaCy (MIT) and
a small spaCy model for English (MIT).

**Numbers.** misaki writes a number out in words with `num2words`, which is
LGPL, and cannot be imported without it. This adapter does not need it:
where it is not installed the engine's own (`speech.numerals`) stands in
its place, and says the same words for English. If it is installed, misaki
uses it as it always did.

**Words no dictionary has.** misaki looks a word up, and hands one it does
not find to espeak-ng, through a package that is GPL-3.0. Without that
package Kokoro leaves the word out of the sentence and says nothing about
it. This adapter does not need it: a word misaki does not have is read by
its spelling (`speech.sounding`), roughly but aloud. If the GPL package is
installed (`pip install phonemizer-fork espeakng-loader`), misaki uses it
as it always did and this adapter stays out of the way.

A voice is asked for by its `profile`: the name of one of the model's own,
as in `af_heart` (the first letter is the language: `a` American English,
`b` British), or the path of a table, a `.safetensors` file as `media
mimic` writes one.

The model and what a line is said with are read from
`CONSTRUCTIVE_MEDIA_MODEL_SPEECH` and `CONSTRUCTIVE_MEDIA_EXTRA_SPEECH`
only when this is the adapter chosen for speech, so that a clone can be
said by one model and a quick voice by this one on the same machine. The
defaults are `{"language": "american", "speed": 1.0}`; the language is
used for a table that does not say what it speaks.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import re
import sys
import types
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..settings import Settings
from . import SpeechError, Voice, numerals, sounding
from .mlx import MlxSpeech, extras

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULTS = {"language": "american", "speed": 1.0}
#: A language as it may be written, and the letter the model knows it by.
LANGUAGES = {"american": "a", "british": "b", "english": "a", "en": "a", "en-us": "a", "en-gb": "b", "a": "a", "b": "b"}
ENGLISH = "ab"
#: The spaCy model misaki reads English with.
READS_WITH = "en_core_web_sm"
#: A voice as a file.
TABLE = ".safetensors"
#: One of the model's own voices, by name: language, then f or m, then a name.
OWN = re.compile(r"[a-z][fm]_\w+")

ESPEAK = "misaki.espeak"


@contextlib.contextmanager
def hushed() -> Iterator[None]:
    """Keep the model's warnings off the screen: what it would warn of is seen to here."""
    was = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        yield
    finally:
        logging.disable(was)


def front_end() -> None:
    """See that what turns English into sounds is here, and say what to install when it is not."""
    try:
        importlib.import_module(numerals.STANDS_FOR)
    except ImportError:
        sys.modules[numerals.STANDS_FOR] = numerals.stand_in()
    try:
        importlib.import_module("misaki.en")
    except ImportError as error:
        raise SpeechError("the kokoro speech adapter reads English with misaki and spaCy: "
                          f"pip install misaki spacy ({error})") from None
    from spacy.util import is_package

    if not is_package(READS_WITH):
        raise SpeechError(f"the kokoro speech adapter reads English with the spaCy model {READS_WITH}: "
                          f"python -m spacy download {READS_WITH}")
    try:
        importlib.import_module(ESPEAK)
    except Exception:  # noqa: BLE001 - not installed, or installed and unable to find its library
        sys.modules[ESPEAK] = _no_espeak()


def _no_espeak() -> types.ModuleType:
    """What stands where misaki's espeak module would be, when that is not installed.

    mlx-audio imports the module before it asks whether it can be used, so
    without it nothing is said at all. With this in its place the model
    loads, finds it has nothing to fall back on, and is given the engine's
    own reader for the words its dictionary does not have.
    """
    stand_in = types.ModuleType(ESPEAK)

    class EspeakFallback:
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("espeak-ng is not installed")

    stand_in.EspeakFallback = EspeakFallback    # type: ignore[attr-defined]
    stand_in.EspeakG2P = EspeakFallback         # type: ignore[attr-defined]
    stand_in.absent = True                      # type: ignore[attr-defined]
    return stand_in


class KokoroSpeech(MlxSpeech):
    """Kokoro, run in this process on Apple silicon."""

    name = "kokoro"
    suffix = TABLE

    def __init__(self, model: str | None = None, *, settings: Settings | None = None) -> None:
        settings = settings or Settings.from_env()
        chosen = settings.adapter("speech") == self.name
        self.model = model or (settings.model("speech") if chosen else None) or DEFAULT_MODEL
        self.options = {**DEFAULTS, **(extras(settings) if chosen else {})}
        self._settings = settings
        self._loaded: Any = None

    def loaded(self) -> Any:
        if self._loaded is None:
            try:
                importlib.import_module("mlx_audio")
            except ImportError:
                raise SpeechError("the kokoro speech adapter needs mlx-audio, which runs on Apple silicon: "
                                  "pip install mlx-audio") from None
            front_end()
            with hushed():
                super().loaded()
        return self._loaded

    # Saying.

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        profile = (voice.profile or "").strip()
        if profile.endswith(TABLE):
            table = Path(profile).expanduser()
            if not table.is_file():
                raise SpeechError(f"the table the {voice.emotion} voice of {voice.persona!r} speaks from is not "
                                  f"there: {table}")
            asked = {"voice": str(table), "lang_code": self._language(_about(table).get("language"))}
        elif profile and all(OWN.fullmatch(one) for one in profile.split(",")):
            asked = {"voice": profile, "lang_code": profile[0]}
        elif voice.reference_audio:
            raise SpeechError(f"the kokoro speech adapter does not clone: the {voice.emotion} voice of "
                              f"{voice.persona!r} is a recording. It speaks in a voice of its own, as in --profile "
                              "af_heart, or in one made to lean towards a recording by `media mimic`")
        else:
            raise SpeechError(f"the {voice.emotion} voice of {voice.persona!r} is not one the kokoro speech adapter "
                              f"has: {profile or 'it has no profile'}. It takes the name of one of the model's own, "
                              "as in af_heart, or the path of a table that `media mimic` wrote")
        return self._said(text, target, seed, asked)

    def _language(self, written: Any = None) -> str:
        asked = str(written or self.options.get("language") or DEFAULTS["language"]).strip().lower()
        if asked in LANGUAGES:
            return LANGUAGES[asked]
        if len(asked) == 1:
            return asked                     # a letter the model has that is not English
        raise SpeechError(f"the kokoro speech adapter does not know the language {asked!r}: american and british "
                          "are the ones it reads with what it asks you to install")

    def _generated(self, model: Any, text: str, asked: dict[str, Any]) -> Iterator[Any]:
        try:
            speed = float(self.options.get("speed") or 1.0)
        except (TypeError, ValueError):
            raise SpeechError(f"the speed in CONSTRUCTIVE_MEDIA_EXTRA_SPEECH is not a number: "
                              f"{self.options.get('speed')!r}") from None
        self._reading(model, asked["lang_code"])
        with hushed():
            yield from model.generate(text=text, speed=speed, **asked)

    def _reading(self, model: Any, language: str) -> None:
        """See that the model has something to read `language` with, down to the words it has never met."""
        if language not in ENGLISH and getattr(sys.modules.get(ESPEAK), "absent", False) and language not in "jz":
            raise SpeechError(f"the kokoro speech adapter reads English by itself. A voice whose language is "
                              f"{language!r} is read through espeak-ng, which is GPL-3.0 and which you install: "
                              "pip install phonemizer-fork espeakng-loader")
        # The model says which language it is making ready, on standard output: a command's output is its own.
        with hushed(), open(os.devnull, "w") as nowhere, contextlib.redirect_stdout(nowhere):
            reads = getattr(model._get_pipeline(language), "g2p", None)
        if language in ENGLISH and reads is not None and getattr(reads, "fallback", False) is None:
            reads.fallback = sounding.Reader(british=language == "b")

    # The model's own voices, for mixing.

    def voices(self) -> list[str]:
        """The voices the model came with that speak English, by name."""
        return sorted(one.stem for one in self._voices().glob("*" + TABLE)
                      if OWN.fullmatch(one.stem) and one.stem[0] in ENGLISH)

    def table(self, voice: str) -> Any:
        """A voice as its numbers: one of the model's own by name, or a table by its path."""
        import mlx.core as mx
        import numpy as np

        held = Path(voice).expanduser() if voice.endswith(TABLE) else self._voices() / (voice + TABLE)
        if not held.is_file():
            raise SpeechError(f"the speech model {self.model} has no voice called {voice}")
        return np.asarray(mx.load(str(held))["voice"], dtype=np.float32)

    def write(self, table: Any, target: str | Path, *, of: Sequence[tuple[str, float]] = ()) -> Path:
        """Write a voice down as a file this adapter speaks from. `of` is what it is a mix of, and how much."""
        import mlx.core as mx
        import numpy as np

        target = Path(target)
        if target.suffix != TABLE:
            raise SpeechError(f"a kokoro voice is written as a {TABLE} file, which {target.name} is not")
        if of:
            british = sum(share for name, share in of if name.startswith("b"))
            language = "b" if british > sum(share for _, share in of) / 2 else "a"
        else:
            language = self._language()
        target.parent.mkdir(parents=True, exist_ok=True)
        mx.save_safetensors(str(target), {"voice": mx.array(np.asarray(table, dtype=np.float32))},
                            metadata={"language": language})
        return target

    def _voices(self) -> Path:
        with self.alone():
            model = self.loaded()
        return Path(str(model.config.model_path)) / "voices"


def _about(table: Path) -> dict[str, str]:
    """What a table says of itself."""
    import mlx.core as mx

    try:
        _, about = mx.load(str(table), return_metadata=True)
    except Exception as error:  # noqa: BLE001
        raise SpeechError(f"{table} is not a voice table: {error}") from None
    return dict(about or {})
