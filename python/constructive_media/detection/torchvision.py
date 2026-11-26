"""A second detector: torchvision's detection models (BSD-3-Clause).

For when PyTorch is already installed, or a torchvision model is the one you
want. Choose it with `CONSTRUCTIVE_MEDIA_ADAPTER_DETECTION=torchvision`. The
model is set with `CONSTRUCTIVE_MEDIA_MODEL_DETECTION` and may be any detection
model torchvision can build by name. The default is the most accurate of them
and wants a GPU; `fasterrcnn_mobilenet_v3_large_320_fpn` is the fast one.

No weights ship with this repository. torchvision downloads them on first use
into its own cache.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..settings import Settings
from . import Detection

DEFAULT_MODEL = "fasterrcnn_resnet50_fpn_v2"
BATCH = 8


class TorchvisionDetector:
    name = "torchvision"

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        *,
        pretrained: bool = True,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings.from_env()
        self.model = model or settings.model("detection") or DEFAULT_MODEL
        self.device = device or settings.device
        self.pretrained = pretrained
        self._network: Any = None
        self._labels: list[str] = []

    def _load(self) -> Any:
        if self._network is None:
            import torch
            from torchvision.models import get_model, get_model_weights

            try:
                weights = get_model_weights(self.model).DEFAULT
            except (ValueError, KeyError):
                raise ValueError(f"torchvision has no detection model named {self.model!r}") from None
            self._labels = list(weights.meta["categories"])
            if self.pretrained:
                network = get_model(self.model, weights=weights, progress=False)
            else:
                network = get_model(self.model, weights=None, weights_backbone=None,
                                    num_classes=len(self._labels))
            if self.device == "auto":
                self.device = ("cuda" if torch.cuda.is_available()
                               else "mps" if torch.backends.mps.is_available() else "cpu")
            self._network = network.eval().to(self.device)
        return self._network

    def detect(self, images: Sequence[Path], confidence: float = 0.35) -> list[list[Detection]]:
        import torch

        network = self._load()
        found: list[list[Detection]] = []
        for start in range(0, len(images), BATCH):
            batch = [_pixels(path).to(self.device) for path in images[start:start + BATCH]]
            with torch.inference_mode():
                results = network(batch)
            for image, result in zip(batch, results):
                height, width = image.shape[-2:]
                found.append([
                    Detection.from_corners(self._labels[label], score, *box, width, height)
                    for box, label, score in zip(
                        result["boxes"].tolist(), result["labels"].tolist(), result["scores"].tolist()
                    )
                    if score >= confidence
                ])
        return found


def _pixels(path: Path) -> Any:
    """An image file as the `channels x height x width` tensor a model takes."""
    import numpy as np
    import torch
    from PIL import Image

    with Image.open(path) as image:
        rgb = np.array(image.convert("RGB"))
    return torch.from_numpy(rgb).permute(2, 0, 1).float().div(255)
