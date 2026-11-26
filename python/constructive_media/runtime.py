"""ONNX Runtime (MIT), for whichever adapter runs a model through it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ModelError

#: The ONNX Runtime provider for each device setting. `auto` and `cpu` run on
#: the CPU, which is where these models measured fastest on Apple hardware.
PROVIDERS = {"cuda": "CUDAExecutionProvider", "mps": "CoreMLExecutionProvider"}


def providers(device: str, onnxruntime: Any) -> list[str]:
    """What to run on for a device setting, the CPU always last."""
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


def available() -> None:
    """Fail now, before anything is downloaded, if ONNX Runtime is not installed."""
    import onnxruntime  # noqa: F401


def session(weights: str | Path, device: str) -> Any:
    """A model file, loaded and ready to run."""
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(weights), options, providers=providers(device, onnxruntime))
