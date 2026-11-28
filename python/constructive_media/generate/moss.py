"""Sounds made on this machine from a description: MOSS-SoundEffect, run by mlx-speech.

[MOSS-SoundEffect](https://huggingface.co/mlx-community/MOSS-SoundEffect-MLX-4bit)
(Apache-2.0) makes a sound from words: rain on a roof, a door, a crowd. It
runs on Apple silicon through [mlx-speech](https://pypi.org/project/mlx-speech/)
(MIT), which wants Python 3.13 or newer, and the engine asks for less. So the
model is run apart, by `moss_runner.py` beside this file, in an interpreter
that has mlx-speech, and nothing here imports a model library. mlx-speech is
installed by you:

    uv venv --python 3.13 ~/tools/sounds
    uv pip install --python ~/tools/sounds/bin/python mlx-speech
    export CONSTRUCTIVE_MEDIA_EXTRA_AUDIO='{"python": "~/tools/sounds/bin/python"}'
    media generate audio "rain on a tin roof, steady" --seconds 6

The interpreter is the one named in the settings, or the engine's own when
mlx-speech is installed beside the engine.

The model is `CONSTRUCTIVE_MEDIA_MODEL_AUDIO`: a folder, or a repository on
Hugging Face, fetched the first time a sound is made.

The same words with the same seed are the same sound. A request with no seed
is given one worked out from its words, so it is the same sound on every
machine and every day; without that, two takes of one description differ by
more than a little.

Two models on one graphics processor at once can stop a machine dead, so a
sound is made while holding the lock that every `media` on the machine shares.

`CONSTRUCTIVE_MEDIA_EXTRA_AUDIO` is a JSON object of what to make a sound
with, in place of the defaults here:

    {"python": "~/tools/sounds/bin/python", "seconds": 6,
     "codec": "mlx-community/MOSS-Audio-Tokenizer-MLX-8bit"}

`seconds` is how long a sound runs when a request does not say. `codec` is
the model that turns what the first one writes into sound.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import gpu
from ..settings import Settings
from . import GenerateError, Request

DEFAULT_MODEL = "mlx-community/MOSS-SoundEffect-MLX-4bit"
DEFAULT_CODEC = "mlx-community/MOSS-Audio-Tokenizer-MLX-8bit"
#: The program that runs the model, in an interpreter of its own.
RUNNER = Path(__file__).with_name("moss_runner.py")
#: The Python that mlx-speech, which that interpreter must have, runs on.
PYTHON = "3.13"
#: How long a sound runs when nobody says, and the longest one the model is asked for.
SECONDS, LONGEST = 4.0, 300.0
DEFAULTS: dict[str, Any] = {"seconds": SECONDS, "codec": DEFAULT_CODEC}
OPTIONS = ("python", "seconds", "codec")


def seed_of(words: str) -> int:
    """The seed a description is given when it comes with none: the same for the same words, everywhere."""
    return int(hashlib.sha256(" ".join(words.split()).encode("utf-8")).hexdigest()[:8], 16) % (2 ** 31)


class Moss:
    """A sound-effect model run by mlx-speech, on Apple silicon."""

    name = "moss"

    def __init__(self, model: str | None = None, *, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()
        self.model = model or self._settings.model("audio") or DEFAULT_MODEL
        self.options = dict(DEFAULTS)
        extra = self._settings.extra("audio")
        if extra:
            try:
                given = json.loads(extra)
            except ValueError as error:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_AUDIO is not JSON: {error}") from None
            if not isinstance(given, dict):
                raise GenerateError('CONSTRUCTIVE_MEDIA_EXTRA_AUDIO should be a JSON object, such as {"seconds": 6}')
            unknown = sorted(set(given) - set(OPTIONS))
            if unknown:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_AUDIO has {', '.join(unknown)}, which the moss "
                                    f"adapter does not know; it knows: {', '.join(OPTIONS)}")
            self.options.update(given)

    def interpreter(self) -> list[str]:
        """What runs the model: an interpreter that has mlx-speech."""
        named = self.options.get("python")
        if named:
            found = Path(str(named)).expanduser()
            if not found.is_file():
                raise GenerateError(f"there is no Python at {found}")
            return [str(found)]
        if importlib.util.find_spec("mlx_speech") is not None:
            return [sys.executable]
        raise GenerateError(
            "the moss adapter runs mlx-speech, which is not installed here. It runs on Apple silicon, with Python "
            f"{PYTHON} or newer: install it somewhere (pip install mlx-speech) and say where its Python is, with "
            'CONSTRUCTIVE_MEDIA_EXTRA_AUDIO=\'{"python": "/path/to/bin/python"}\''
        )

    def seconds(self, request: Request) -> float:
        return float(request.seconds or self.options["seconds"])

    def check(self, request: Request) -> None:
        """Refuse what cannot be made, before anything is run."""
        if request.medium != "audio":
            raise GenerateError(f"the moss adapter makes sounds, not {request.medium}")
        seconds = self.seconds(request)
        if not 0 < seconds <= LONGEST:
            raise GenerateError(f"a sound of {seconds:g} seconds cannot be made: the longest is {LONGEST:g}")
        self.interpreter()

    def asked(self, request: Request, out: Path) -> dict[str, Any]:
        """What the runner is told, for one request."""
        return {
            "prompt": request.prompt, "seconds": self.seconds(request),
            "seed": seed_of(request.prompt) if request.seed is None else int(request.seed),
            "model": self.model, "codec": str(self.options["codec"]), "out": str(out),
        }

    def command(self, request: Request, out: Path) -> list[str]:
        """The interpreter, the runner and what it is told. -P keeps the files beside the runner out of its way."""
        self.check(request)
        return [*self.interpreter(), "-P", str(RUNNER), json.dumps(self.asked(request, out))]

    def make(self, request: Request, target: Path) -> Path:
        self.check(request)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sound-", dir=target.parent) as folder:
            out = Path(folder) / f"sound{target.suffix}"
            command = self.command(request, out)
            with gpu.alone(self._settings):
                done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if done.returncode or not out.is_file():
                raise GenerateError(f"the moss adapter could not make the sound: {_reason(done)}")
            out.replace(target)
        return target


def _reason(done: subprocess.CompletedProcess[str]) -> str:
    """Why the runner failed, in one line: what it said, or the last thing whatever ran it did."""
    printed = (done.stdout or "").strip().splitlines()
    if printed:
        try:
            said = json.loads(printed[-1])
        except ValueError:
            said = None
        if isinstance(said, dict) and said.get("error"):
            return str(said["error"])
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[-1] if lines else "it gave no reason"
