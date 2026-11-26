"""The default embedder: speaker models through ONNX Runtime (MIT).

It runs any speaker model exported to ONNX in the shape WeSpeaker publishes:
`frames x 80` log mel features in, one row of numbers out. The models it knows
by name are WeSpeaker's, trained on VoxCeleb and published under Apache-2.0.

`CONSTRUCTIVE_MEDIA_MODEL_SPEAKER` picks the model: a name from `KNOWN`, or a
folder holding `model.onnx`.

No weights ship with this repository. A known model is downloaded once into
the models folder and checked against a pinned hash.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import runtime
from ..models import ModelError, Remote, fetch
from ..settings import Settings
from . import RATE
from .fbank import BANDS, fbank, levelled

DEFAULT_MODEL = "campplus"
WEIGHTS = "model.onnx"
#: How many sounds of one length are run at once.
BATCH = 32
#: Fewer frames than this and there is nothing for a model to read.
FEWEST_FRAMES = 10

_HUB = "https://huggingface.co/Wespeaker"


@dataclass(frozen=True)
class Known:
    """A published model: what it is, its license and where it is."""

    summary: str
    license: str
    weights: Remote


KNOWN: dict[str, Known] = {
    "campplus": Known(
        summary="CAM++: the fastest, and as good as any on a second or two of speech",
        license="Apache-2.0",
        weights=Remote(
            f"{_HUB}/wespeaker-voxceleb-campplus/resolve/acf623ad8ca746e50baa432255cf8fc57c669c45/voxceleb_CAM%2B%2B.onnx",
            "b50810498b5bcf5773d086f6993d344476bd0c88b566a41e8d801aaf8461efad",
        ),
    ),
    "resnet34": Known(
        summary="ResNet34: as good, at about a third of the speed",
        license="Apache-2.0",
        weights=Remote(
            f"{_HUB}/wespeaker-voxceleb-resnet34/resolve/ff1ac5bca8ef11e90662b879aa923979e0bd277b/voxceleb_resnet34.onnx",
            "9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1",
        ),
    ),
    "resnet221_lm": Known(
        summary="ResNet221: the surest that two voices are two, at a twelfth of the speed",
        license="Apache-2.0",
        weights=Remote(
            f"{_HUB}/wespeaker-voxceleb-resnet221-LM/resolve/26732f43821f238bfb258c5ef54924a798e62118/voxceleb_resnet221_LM.onnx",
            "e6b8c14c022288ca186de39f038a9bd9404be4f174ac8a6a3b8dbf229c8f607d",
        ),
    ),
}


class OnnxSpeaker:
    name = "onnx"

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings.from_env()
        self.model = model or settings.model("speaker") or DEFAULT_MODEL
        self.device = device or settings.device
        self._models_dir = settings.models_dir
        self._session: Any = None
        self._input = ""
        self._output = ""

    def weights(self) -> Path:
        """The model's file, downloading a known model if needed."""
        if self.model in KNOWN:
            return fetch(KNOWN[self.model].weights, self._models_dir / "speaker" / self.model / WEIGHTS)
        weights = Path(self.model).expanduser() / WEIGHTS
        if not weights.is_file():
            raise ModelError(
                f"no speaker model {self.model!r}: it is not one of {', '.join(KNOWN)}, "
                f"and as a folder it lacks {WEIGHTS}"
            )
        return weights

    def _load(self) -> Any:
        if self._session is None:
            runtime.available()
            weights = self.weights()
            session = runtime.session(weights, self.device)
            inputs, outputs = session.get_inputs(), session.get_outputs()
            shape = inputs[0].shape if len(inputs) == 1 else []
            if len(shape) != 3 or shape[2] != BANDS or len(outputs) != 1:
                raise ModelError(
                    f"{weights} is not a speaker model this adapter can read: "
                    f"it wants one input of frames by {BANDS} features and one output"
                )
            self._input, self._output = inputs[0].name, outputs[0].name
            self._session = session
        return self._session

    def embed(self, sounds: Sequence[Any]) -> Any:
        import numpy as np

        if not len(sounds):
            return np.zeros((0, 0), dtype=np.float32)
        session = self._load()
        features = [levelled(fbank(sound, RATE)) for sound in sounds]
        for sound, feature in zip(sounds, features):
            if len(feature) < FEWEST_FRAMES:
                raise ValueError(f"{len(sound) / RATE:.2f} seconds of sound is too little to tell a voice from")
        # Sounds of one length go through together; windows of speech all are.
        alike: dict[int, list[int]] = {}
        for index, feature in enumerate(features):
            alike.setdefault(len(feature), []).append(index)
        rows: list[Any] = [None] * len(features)
        for indexes in alike.values():
            for start in range(0, len(indexes), BATCH):
                batch = indexes[start:start + BATCH]
                (answer,) = session.run([self._output], {self._input: np.stack([features[i] for i in batch])})
                for index, row in zip(batch, answer):
                    rows[index] = row
        return unit(np.stack(rows).astype(np.float32))


def unit(rows: Any) -> Any:
    """Each row at length one, so that a dot product is a cosine."""
    import numpy as np

    return rows / np.maximum(np.linalg.norm(rows, axis=-1, keepdims=True), 1e-12)
