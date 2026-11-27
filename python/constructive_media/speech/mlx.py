"""Speech on this machine, with no server: models run through MLX.

[MLX](https://github.com/ml-explore/mlx) runs models on Apple silicon, and
[mlx-audio](https://github.com/Blaizzy/mlx-audio) (MIT) runs speech models on
it. This adapter loads one in the engine's own process, so a Mac says a
script with nothing else running:

    pip install "constructive-media[audio]" mlx-audio
    CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH=mlx

mlx-audio is not one of the engine's extras. It is MIT itself, and it brings
two packages under MPL-2.0 with it (tqdm and certifi), so the engine does
not install it for you: you do, knowing that.

The model is `CONSTRUCTIVE_MEDIA_MODEL_SPEECH`: a folder, or the name of a
repository on Hugging Face that mlx-audio can load. Left out, it is
Qwen3-TTS (Apache-2.0), which clones a voice from a recording of it.

A voice with a reference recording is cloned from it: the recording and the
words said in it go to the model with every line. A voice with only a
`profile` is asked for by that name, for a model that has voices of its own.

Two models on one graphics processor at once can stop a machine dead, so a
line is said while holding a lock that every `media` on the machine shares.

`CONSTRUCTIVE_MEDIA_EXTRA_SPEECH` is a JSON object of what to say a line
with, in place of the defaults here: `{"temperature": 0.3, "top_p": 0.8,
"language": "english"}`.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .. import gpu
from ..audio import pcm
from ..settings import Settings
from . import SpeechError, Voice

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
#: What a line is said with. A low temperature keeps a clone close to its recording.
DEFAULTS = {"temperature": 0.3, "top_p": 0.8, "language": "english"}
#: The rate a recording is handed to the model at.
REFERENCE_RATE = 24_000
#: How much a model that writes sound as tokens writes for a second of it, and the slowest anybody speaks.
TOKENS_A_SECOND = 12.5
SLOWEST_WORDS_A_SECOND = 1.0
LEAST_TOKENS = 96


def most_tokens(text: str) -> int:
    """The most a line may run to. A model that loses its place talks on for minutes; this stops it early."""
    words = max(1, len(re.findall(r"\w+", text)))
    return max(LEAST_TOKENS, math.ceil(words / SLOWEST_WORDS_A_SECOND * TOKENS_A_SECOND))


@contextlib.contextmanager
def _quietly() -> Iterator[None]:
    """Keep what a model's loader prints off the screen: a command's output is its own."""
    try:
        from huggingface_hub.utils import disable_progress_bars
        from transformers.utils import logging as said

        disable_progress_bars()
        said.set_verbosity_error()
    except ImportError:
        pass
    with open(os.devnull, "w") as nowhere, contextlib.redirect_stdout(nowhere), contextlib.redirect_stderr(nowhere):
        yield


class MlxSpeech:
    """A speech model run in this process, on Apple silicon."""

    name = "mlx"

    def __init__(self, model: str | None = None, *, settings: Settings | None = None) -> None:
        settings = settings or Settings.from_env()
        self.model = model or settings.model("speech") or DEFAULT_MODEL
        self.options = dict(DEFAULTS)
        extra = settings.extra("speech")
        if extra:
            try:
                given = json.loads(extra)
            except ValueError as error:
                raise SpeechError(f"CONSTRUCTIVE_MEDIA_EXTRA_SPEECH is not JSON: {error}") from None
            if not isinstance(given, dict):
                raise SpeechError('CONSTRUCTIVE_MEDIA_EXTRA_SPEECH should be a JSON object, such as {"temperature": 0.3}')
            self.options.update(given)
        self._settings = settings
        self._loaded: Any = None

    # The model.

    def loaded(self) -> Any:
        if self._loaded is None:
            try:
                from mlx_audio.tts.utils import load_model
            except ImportError:
                raise SpeechError("the mlx speech adapter needs mlx-audio, which runs on Apple silicon: "
                                  "pip install mlx-audio") from None
            try:
                with _quietly():
                    self._loaded = load_model(self.model)
            except Exception as error:  # noqa: BLE001 - whatever a model fails to load with, say it plainly
                raise SpeechError(f"the speech model {self.model} could not be loaded: {error}") from None
        return self._loaded

    @contextlib.contextmanager
    def alone(self) -> Iterator[None]:
        """Hold the lock that keeps two models off the graphics processor at once."""
        with gpu.alone(self._settings):
            yield

    # Saying.

    def say(self, text: str, target: Path, *, voice: Voice, seed: int | None = None) -> Path:
        asked: dict[str, Any] = {}
        if voice.reference_audio:
            recording = Path(voice.reference_audio).expanduser()
            if not recording.is_file():
                raise SpeechError(f"the recording the {voice.emotion} voice of {voice.persona!r} was made from "
                                  f"is not there: {recording}")
            asked["ref_audio"] = self._heard(recording)
            if voice.reference_text:
                asked["ref_text"] = voice.reference_text
        elif voice.profile:
            asked["voice"] = voice.profile
        else:
            raise SpeechError(f"the {voice.emotion} voice of {voice.persona!r} has neither a recording to clone "
                              "nor a profile, the name a model knows one of its own voices by")
        return self._said(text, target, seed, asked)

    def embed(self, recording: str | Path) -> list[float]:
        """A voice as the numbers this model hears it by, from a recording of it."""
        with self.alone():
            model = self.loaded()
            if getattr(model, "speaker_encoder", None) is None:
                raise SpeechError(f"the speech model {self.model} does not hear a voice as numbers: it cannot blend")
            return [float(one) for one in model.extract_speaker_embedding(
                self._heard(Path(recording)), sr=REFERENCE_RATE).reshape(-1).tolist()]

    def say_as(self, text: str, target: Path, *, embedding: list[float], seed: int | None = None) -> Path:
        """Say `text` in the voice that `embedding` is, as `embed` gives one or as two of them blended."""
        import mlx.core as mx

        forced = mx.array([embedding], dtype=mx.float32)
        # The model takes a voice only as a recording. It is given silence, and told what it heard in it.
        silence = mx.zeros((REFERENCE_RATE,), dtype=mx.float32)
        return self._said(text, target, seed, {"ref_audio": silence}, embedding=forced)

    def _heard(self, recording: Path) -> Any:
        import mlx.core as mx

        return mx.array(pcm.read(recording, rate=REFERENCE_RATE, stereo=False))

    def _said(self, text: str, target: Path, seed: int | None, asked: dict[str, Any], *, embedding: Any = None) -> Path:
        import numpy as np

        options = dict(self.options)
        language = options.pop("language", None)
        with self.alone():
            import mlx.core as mx

            model = self.loaded()
            if seed is not None:
                np.random.seed(seed % (2 ** 32))
                mx.random.seed(seed)
            put_back = None
            if embedding is not None:
                put_back = model.extract_speaker_embedding
                model.extract_speaker_embedding = lambda *_, **__: embedding
            try:
                parts = [np.asarray(part.audio, dtype=np.float32).reshape(-1)
                         for part in model.generate(text=text, lang_code=language or "auto",
                                                    max_tokens=most_tokens(text), **options, **asked)]
            except SpeechError:
                raise
            except Exception as error:  # noqa: BLE001
                raise SpeechError(f"the speech model {self.model} could not say the line: {error}") from None
            finally:
                if put_back is not None:
                    model.extract_speaker_embedding = put_back
            rate = int(getattr(model, "sample_rate", REFERENCE_RATE))
        if not parts or not sum(len(part) for part in parts):
            raise SpeechError(f"the speech model {self.model} answered with nothing to hear")
        return pcm.write(Path(target), np.concatenate(parts), rate)
