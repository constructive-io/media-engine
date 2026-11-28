"""Video made on this machine, with no server: Wan run through diffusers.

[diffusers](https://github.com/huggingface/diffusers) (Apache-2.0) runs
diffusion models on PyTorch (BSD-3-Clause). This adapter loads a Wan model in
the engine's own process and writes what it makes with the engine's ffmpeg:

    pip install torch diffusers transformers ftfy
    media generate video "fog lifting off a harbour at dawn" --seconds 2

Those are not among the engine's extras. They are Apache-2.0 and BSD
themselves, and they bring two packages under MPL-2.0 with them (tqdm and
certifi), so the engine does not install them for you: you do, knowing that.

The model is `CONSTRUCTIVE_MEDIA_MODEL_VIDEO`: a folder, or the name of a
repository on Hugging Face in diffusers' layout that holds a Wan model which
starts from words. Left out, it is Wan 2.1 T2V 1.3B (Apache-2.0), the
smallest of them: 480p, and slow but possible on a laptop. diffusers fetches
it the first time a piece is made.

It is not quick. On an Apple M4 Max, two seconds of 832x480 at the fifteen
passes this makes by default took four and a half minutes, and one second
took under two. More passes are better and slower.

Two models on one graphics processor at once can stop a machine dead, so a
piece is made while holding the lock that every `media` on the machine shares.

`CONSTRUCTIVE_MEDIA_EXTRA_VIDEO` is a JSON object of what to make a piece
with, in place of the defaults here:

    {"steps": 15, "fps": 16, "guidance": 5.0, "negative": "blurry, low quality, distorted"}

Both settings are read only while this is the adapter chosen for video, which
it is until `CONSTRUCTIVE_MEDIA_ADAPTER_VIDEO` names another. With another
chosen, they are that adapter's, and this one asked for by name
(`--adapter diffusers`) runs on its defaults.

It starts from words alone. A still made to move is the `ltx` adapter in
[media-engine-plugins](https://github.com/constructive-io/media-engine-plugins),
whose model is published under terms outside the engine's rule.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .. import ffmpeg, gpu
from ..settings import Settings
from . import GenerateError, Request

DEFAULT_MODEL = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
#: The size Wan's smallest model was trained at. Sides are multiples of 16.
WIDTH, HEIGHT = 832, 480
SIDE = 16
SECONDS = 2.0
#: The model writes four frames at a time after the first, so a piece is 4n + 1 frames long.
FRAMES_TOGETHER = 4
LEAST_FRAMES = 9
#: What a piece is made with. Wan is trained at 16 frames a second.
DEFAULTS: dict[str, Any] = {"steps": 15, "fps": 16, "guidance": 5.0, "negative": "blurry, low quality, distorted"}
DEVICES = ("mps", "cuda", "cpu")


def frames_for(seconds: float, fps: int) -> int:
    """How many frames the model is asked for: the nearest count it can write, and never too few to move."""
    return max(LEAST_FRAMES, round(seconds * fps / FRAMES_TOGETHER) * FRAMES_TOGETHER + 1)


class DiffusersVideo:
    """A Wan model run in this process through diffusers."""

    name = "diffusers"

    def __init__(self, model: str | None = None, *, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()
        # With another adapter chosen for video, the model and the options that are set are that one's.
        chosen = self._settings.adapter("video") in (None, self.name)
        self.model = model or (self._settings.model("video") if chosen else None) or DEFAULT_MODEL
        self.options = dict(DEFAULTS)
        extra = self._settings.extra("video") if chosen else None
        if extra:
            try:
                given = json.loads(extra)
            except ValueError as error:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_VIDEO is not JSON: {error}") from None
            if not isinstance(given, dict):
                raise GenerateError('CONSTRUCTIVE_MEDIA_EXTRA_VIDEO should be a JSON object, such as {"steps": 15}')
            unknown = sorted(set(given) - set(DEFAULTS))
            if unknown:
                raise GenerateError(f"CONSTRUCTIVE_MEDIA_EXTRA_VIDEO has {', '.join(unknown)}, which the diffusers "
                                    f"adapter does not know; it knows: {', '.join(DEFAULTS)}")
            self.options.update(given)
        self._loaded: Any = None

    # The model.

    def device(self, torch: Any) -> str:
        """What the model runs on: what was asked for, or the best there is."""
        have = {"mps": torch.backends.mps.is_available(), "cuda": torch.cuda.is_available(), "cpu": True}
        wanted = self._settings.device
        if wanted == "auto":
            return next(name for name in DEVICES if have[name])
        if wanted not in have:
            raise GenerateError(f"the diffusers adapter runs on one of {', '.join(DEVICES)}, not on {wanted!r}")
        if not have[wanted]:
            raise GenerateError(f"this PyTorch cannot use {wanted!r} on this machine")
        return wanted

    def loaded(self) -> Any:
        if self._loaded is None:
            # A few of Wan's steps are not written for Apple's graphics processors yet; those run on the CPU.
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            try:
                import torch
                from diffusers import AutoencoderKLWan, WanPipeline
            except ImportError:
                raise GenerateError("the diffusers video adapter needs PyTorch and diffusers: "
                                    "pip install torch diffusers transformers ftfy") from None
            device = self.device(torch)
            try:
                # The part that turns numbers into pictures is kept at full precision: at half it writes grey.
                decoder = AutoencoderKLWan.from_pretrained(self.model, subfolder="vae", torch_dtype=torch.float32)
                pipeline = WanPipeline.from_pretrained(
                    self.model, vae=decoder,
                    torch_dtype=torch.float32 if device == "cpu" else torch.bfloat16,
                ).to(device)
                pipeline.set_progress_bar_config(disable=True)
                if device == "mps":
                    pipeline.scheduler.step = on_processor(torch, pipeline.scheduler.step)
            except Exception as error:  # noqa: BLE001 - whatever a model fails to load with, say it plainly
                raise GenerateError(f"the video model {self.model} could not be loaded: {error}") from None
            self._loaded = (torch, pipeline)
        return self._loaded

    # Making.

    def check(self, request: Request) -> None:
        """Refuse what cannot be made, before a model is loaded."""
        if request.medium != "video":
            raise GenerateError("the diffusers adapter makes video, not pictures")
        if request.image is not None:
            raise GenerateError(f"the video model {self.model} starts from words, not from a picture; the ltx "
                                "adapter in media-engine-plugins makes a still move")
        width, height = request.width or WIDTH, request.height or HEIGHT
        if width % SIDE or height % SIDE:
            raise GenerateError(f"{width}x{height} cannot be made: each side is a multiple of {SIDE}, "
                                f"such as {round(width / SIDE) * SIDE}x{round(height / SIDE) * SIDE}")

    def make(self, request: Request, target: Path) -> Path:
        import numpy as np

        self.check(request)
        width, height = request.width or WIDTH, request.height or HEIGHT
        fps = int(self.options["fps"])
        count = frames_for(request.seconds or SECONDS, fps)
        with gpu.alone(self._settings):
            torch, pipeline = self.loaded()
            asked: dict[str, Any] = {}
            if request.seed is not None:
                asked["generator"] = torch.Generator(device="cpu").manual_seed(int(request.seed))
            try:
                made = pipeline(
                    prompt=request.prompt, negative_prompt=self.options["negative"] or None,
                    height=height, width=width, num_frames=count,
                    num_inference_steps=int(request.steps or self.options["steps"]),
                    guidance_scale=float(self.options["guidance"]), output_type="np", **asked,
                ).frames[0]
            except Exception as error:  # noqa: BLE001
                raise GenerateError(f"the video model {self.model} could not make the piece: {error}") from None
        frames = np.asarray(made)
        if frames.ndim != 4 or frames.shape[-1] != 3 or not len(frames):
            raise GenerateError(f"the video model {self.model} answered with nothing to show")
        return written(frames, fps, Path(target))


def on_processor(torch: Any, step: Any) -> Any:
    """A scheduler's step, with its arithmetic done on the processor.

    Between passes a scheduler multiplies what the model made by one number
    picked out of a list it keeps on the processor. PyTorch 2.13 on Apple's
    graphics processors reads the first number of that list whichever was
    picked, and says nothing: every pass after the first goes wrong and the
    piece comes out burnt white. What a step works on is small, so it is moved
    to the processor for the step and back, which takes no time to speak of.
    """

    def stepped(model_output: Any, timestep: Any, sample: Any, *rest: Any, **more: Any) -> Any:
        out = step(model_output.to("cpu", torch.float32), timestep, sample.to("cpu", torch.float32), *rest, **more)
        if isinstance(out, tuple):
            return (out[0].to(sample.device, sample.dtype), *out[1:])
        out.prev_sample = out.prev_sample.to(sample.device, sample.dtype)
        return out

    return stepped


def written(frames: Any, fps: int, target: Path) -> Path:
    """Frames, as numbers from nought to one, written as a file that plays anywhere."""
    import numpy as np

    _, height, width, _ = frames.shape
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frames-") as folder:
        raw = Path(folder) / "frames.rgb"
        with open(raw, "wb") as file:
            for frame in frames:
                file.write((np.clip(np.nan_to_num(frame), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes())
        ffmpeg.encode(
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", raw,
            # Not the bits a film of a page needs: moving pictures take several times as many.
            "-c:v", "{vcodec}", "-b:v", str(max(2_000_000, int(width * height * fps * 0.6))),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", target,
        )
    return target
