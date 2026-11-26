"""The default detector: transformer detectors through ONNX Runtime (MIT).

It runs any detector exported to ONNX in the DETR family's shape: one image
tensor in, `logits` and `pred_boxes` out, which is how D-FINE, RT-DETR and
their relatives are published. The models it knows by name are D-FINE
(Apache-2.0): real-time on a CPU, with no non-maximum suppression step.

`CONSTRUCTIVE_MEDIA_MODEL_DETECTION` picks the model: a name from `KNOWN`, or a
folder holding `model.onnx` and the `config.json` that names its labels.

No weights ship with this repository. A known model is downloaded once into
the models folder and checked against a pinned hash.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import ModelError, Remote, fetch
from ..settings import Settings
from . import Detection

DEFAULT_MODEL = "dfine_n"
INPUT_SIZE = 640

#: The files a model folder holds.
WEIGHTS = "model.onnx"
CONFIG = "config.json"

_HUB = "https://huggingface.co/onnx-community"


@dataclass(frozen=True)
class Known:
    """A published model: what it is, its license and where its files are."""

    summary: str
    license: str
    files: Mapping[str, Remote]


KNOWN: dict[str, Known] = {
    "dfine_n": Known(
        summary="D-FINE nano, trained on COCO: the fastest",
        license="Apache-2.0",
        files={
            WEIGHTS: Remote(
                f"{_HUB}/dfine_n_coco-ONNX/resolve/380d2839c327efaf65dd0fe0c2c10ab7fadd5473/onnx/model.onnx",
                "0f684f409618ee8a822410e754a29caa817d1aa16283ce89cad936d0a48e2f35",
            ),
            CONFIG: Remote(
                f"{_HUB}/dfine_n_coco-ONNX/resolve/380d2839c327efaf65dd0fe0c2c10ab7fadd5473/config.json",
                "a5c7533f3b72be6bb102b93e1b34ca3643af4e0590408a7881543cbb0aa80c4c",
            ),
        },
    ),
    "dfine_s": Known(
        summary="D-FINE small, trained on COCO: finds more, at about half the speed",
        license="Apache-2.0",
        files={
            WEIGHTS: Remote(
                f"{_HUB}/dfine_s_coco-ONNX/resolve/a3cf03147a9b86c78475139115c8ac142577352d/onnx/model.onnx",
                "cd8a49a945feda6d28c6304ae8ae85c2759ba1d78a5a83a22c5ce8db82ef7238",
            ),
            CONFIG: Remote(
                f"{_HUB}/dfine_s_coco-ONNX/resolve/a3cf03147a9b86c78475139115c8ac142577352d/config.json",
                "9338ef3863d6e95627d4ab06009fa85b1dd523b346b5c3595de2b08862136e99",
            ),
        },
    ),
}

#: The ONNX Runtime provider for each device setting. `auto` and `cpu` run on
#: the CPU, which is where these models measured fastest on Apple hardware.
PROVIDERS = {"cuda": "CUDAExecutionProvider", "mps": "CoreMLExecutionProvider"}


class OnnxDetector:
    name = "onnx"

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings.from_env()
        self.model = model or settings.model("detection") or DEFAULT_MODEL
        self.device = device or settings.device
        self._models_dir = settings.models_dir
        self._session: Any = None
        self._input = ""
        self._labels: dict[int, str] = {}

    def folder(self) -> Path:
        """The folder holding the model's files, downloading a known model if needed."""
        if self.model in KNOWN:
            folder = self._models_dir / "detection" / self.model
            for name, remote in KNOWN[self.model].files.items():
                fetch(remote, folder / name)
            return folder
        folder = Path(self.model).expanduser()
        missing = [name for name in (WEIGHTS, CONFIG) if not (folder / name).is_file()]
        if missing:
            raise ModelError(
                f"no detection model {self.model!r}: it is not one of {', '.join(KNOWN)}, "
                f"and as a folder it lacks {' and '.join(missing)}"
            )
        return folder

    def _load(self) -> Any:
        if self._session is None:
            import onnxruntime

            folder = self.folder()
            labels = json.loads((folder / CONFIG).read_text()).get("id2label")
            if not labels:
                raise ModelError(f"{folder / CONFIG} names no labels (it has no id2label)")
            self._labels = {int(index): label for index, label in labels.items()}
            options = onnxruntime.SessionOptions()
            options.log_severity_level = 3
            session = onnxruntime.InferenceSession(
                str(folder / WEIGHTS), options, providers=_providers(self.device, onnxruntime)
            )
            outputs = {output.name for output in session.get_outputs()}
            if not {"logits", "pred_boxes"} <= outputs:
                raise ModelError(
                    f"{folder / WEIGHTS} is not a detector this adapter can read: "
                    f"it gives {', '.join(sorted(outputs))}, not logits and pred_boxes"
                )
            self._input = session.get_inputs()[0].name
            self._session = session
        return self._session

    def detect(self, images: Sequence[Path], confidence: float = 0.35) -> list[list[Detection]]:
        if not images:
            return []
        session = self._load()
        found: list[list[Detection]] = []
        for path in images:
            logits, boxes = session.run(["logits", "pred_boxes"], {self._input: _pixels(path)[None]})
            found.append(decode(logits[0], boxes[0], self._labels, confidence))
        return found


def decode(logits: Any, boxes: Any, labels: Mapping[int, str], confidence: float) -> list[Detection]:
    """One image's detections from the model's raw answer, surest first.

    `logits` is `queries x classes` and `boxes` is `queries x 4`: centre, width
    and height as shares of the image. Each query speaks for its likeliest class.
    """
    import numpy as np

    scores = 1.0 / (1.0 + np.exp(-np.asarray(logits, dtype=np.float64)))
    best = scores.argmax(axis=-1)
    score = scores.max(axis=-1)
    found = []
    for query in np.argsort(-score, kind="stable"):
        if score[query] < confidence:
            break
        cx, cy, w, h = (float(v) for v in boxes[query])
        found.append(Detection.from_corners(
            labels.get(int(best[query]), str(int(best[query]))), float(score[query]),
            cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 1.0, 1.0,
        ))
    return found


def _pixels(path: Path) -> Any:
    """An image file as the `3 x size x size` array the model takes.

    The image is stretched to a square, so the box shares the model answers
    with are already shares of the original frame.
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        square = image.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR)
    return np.asarray(square, dtype=np.float32).transpose(2, 0, 1) / 255.0


def _providers(device: str, onnxruntime: Any) -> list[str]:
    available = onnxruntime.get_available_providers()
    wanted = PROVIDERS.get(device)
    if device == "auto" and PROVIDERS["cuda"] in available:
        wanted = PROVIDERS["cuda"]
    if wanted and wanted not in available:
        raise ModelError(
            f"this ONNX Runtime cannot use {device!r} ({wanted} is not installed); "
            f"it offers {', '.join(available)}"
        )
    return [*([wanted] if wanted else []), "CPUExecutionProvider"]
