"""Pictures made on this machine, with no server: FLUX.2 run by the mflux programs.

[mflux](https://github.com/filipstrand/mflux) (MIT) runs image models on
Apple silicon through MLX. It is run as a separate program,
`mflux-generate-flux2`, and is not part of this package; nothing here imports
a model library.

    pip install mflux          # or: uv tool install mflux
    media generate image "a harbour at dawn, fog over the water"

The model is `CONSTRUCTIVE_MEDIA_MODEL_IMAGE`: a name mflux knows, a
repository on Hugging Face, or a folder. Left out, it is FLUX.2 klein 4B
(Apache-2.0), which mflux fetches the first time a picture is made. Other
models mflux knows carry terms of their own, some of them not for commercial
use: which one runs is yours to choose and to check.

A request that starts from a picture is handed to `mflux-generate-flux2-edit`,
which mflux installs beside the first.

Two models on one graphics processor at once can stop a machine dead, so a
picture is made while holding the lock that every `media` on the machine
shares.

`CONSTRUCTIVE_MEDIA_EXTRA_IMAGE` is a JSON object of what to make a picture
with, in place of the defaults here:

    {"program": "~/tools/bin/mflux-generate-flux2", "steps": 8, "quantize": 8,
     "guidance": 1.0, "base_model": "flux2-klein-4b"}

`program` is where the program is, when it is not on the path. `base_model`
is for a model given as a repository or a folder: which of mflux's own it is
a kind of.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .. import gpu
from ..settings import Settings
from . import GenerateError, Request

DEFAULT_MODEL = "flux2-klein-4b"
PROGRAM = "mflux-generate-flux2"
#: The program for a request that starts from a picture, installed beside the first.
EDIT_PROGRAM = "mflux-generate-flux2-edit"
#: A wide picture, the shape of a frame of video. Sides are multiples of 16.
WIDTH, HEIGHT = 1344, 768
SIDE = 16
#: What a picture is made with.
DEFAULTS: dict[str, Any] = {"steps": 4}
OPTIONS = ("program", "steps", "quantize", "guidance", "base_model")


class Mflux:
    """An image model run by the mflux program, on Apple silicon."""

    name = "mflux"

    def __init__(self, model: str | None = None, *, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()
        self.model = model or self._settings.model("image") or DEFAULT_MODEL
        self.options = dict(DEFAULTS)
        extra = self._settings.extra("image")
        if extra:
            try:
                given = json.loads(extra)
            except ValueError as error:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_IMAGE is not JSON: {error}") from None
            if not isinstance(given, dict):
                raise GenerateError('CONSTRUCTIVE_MEDIA_EXTRA_IMAGE should be a JSON object, such as {"steps": 8}')
            unknown = sorted(set(given) - set(OPTIONS))
            if unknown:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_IMAGE has {', '.join(unknown)}, which the mflux "
                                    f"adapter does not know; it knows: {', '.join(OPTIONS)}")
            self.options.update(given)

    def program(self, name: str = PROGRAM) -> str:
        """Where one of mflux's programs is: beside the one that was named, or on the path."""
        named = self.options.get("program")
        if named:
            first = Path(str(named)).expanduser()
            found = first if name == PROGRAM else first.with_name(name)
            if not found.is_file():
                raise GenerateError(f"there is no {name} at {found}")
            return str(found)
        found = shutil.which(name)
        if found is None:
            raise GenerateError(
                f"the mflux adapter runs {name}, which was not found. mflux runs on Apple silicon: "
                "pip install mflux. Where it is not on the path, say where it is: "
                'CONSTRUCTIVE_MEDIA_EXTRA_IMAGE=\'{"program": "/path/to/' + PROGRAM + "\"}'"
            )
        return found

    def check(self, request: Request) -> None:
        """Refuse what cannot be made, before anything is run."""
        if request.medium != "image":
            raise GenerateError(f"the mflux adapter makes pictures, not {request.medium}")
        width, height = request.width or WIDTH, request.height or HEIGHT
        if width % SIDE or height % SIDE:
            raise GenerateError(f"{width}x{height} cannot be made: each side is a multiple of {SIDE}, "
                                f"such as {round(width / SIDE) * SIDE}x{round(height / SIDE) * SIDE}")

    def command(self, request: Request, out: Path) -> list[str]:
        """The program and everything it is given, for one request."""
        self.check(request)
        width, height = request.width or WIDTH, request.height or HEIGHT
        command = [
            self.program(EDIT_PROGRAM if request.image is not None else PROGRAM),
            "--model", self.model, "--prompt", request.prompt,
            "--steps", str(request.steps or self.options["steps"]),
            "--width", str(width), "--height", str(height), "--output", str(out),
        ]
        if request.seed is not None:
            command += ["--seed", str(request.seed)]
        if request.image is not None:
            command += ["--image-paths", str(request.image)]
        for option, flag in (("base_model", "--base-model"), ("quantize", "--quantize"), ("guidance", "--guidance")):
            if self.options.get(option) is not None:
                command += [flag, str(self.options[option])]
        return command

    def make(self, request: Request, target: Path) -> Path:
        self.check(request)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written somewhere of its own first: the program renames what it writes sooner than write over a file.
        with tempfile.TemporaryDirectory(prefix="picture-", dir=target.parent) as folder:
            out = Path(folder) / f"picture{target.suffix}"
            command = self.command(request, out)
            with gpu.alone(self._settings):
                done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if done.returncode or not out.is_file():
                lines = (done.stderr or done.stdout or "").strip().splitlines()
                raise GenerateError(f"{Path(command[0]).name} could not make the picture: "
                                    f"{lines[-1] if lines else 'it gave no reason'}")
            out.replace(target)
        return target
