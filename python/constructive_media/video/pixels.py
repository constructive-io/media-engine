"""Arithmetic on pixels. Everything here takes and returns numpy arrays."""

from __future__ import annotations

import numpy as np


def image(raw: bytes, width: int, height: int, channels: int = 3) -> np.ndarray:
    """Raw bytes as a `height x width x channels` array, or `height x width`."""
    shape = (height, width, channels) if channels > 1 else (height, width)
    return np.frombuffer(raw, dtype=np.uint8).reshape(shape)


def hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees (0 to 360), saturation and value (each 0 to 1)."""
    pixels = rgb.astype(np.float32) / 255.0
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    # Pairwise, because a reduction over an axis three long is far slower.
    value = np.maximum(np.maximum(red, green), blue)
    spread = value - np.minimum(np.minimum(red, green), blue)
    coloured = spread > 0
    safe = np.where(coloured, spread, 1.0)
    towards_red = (green - blue) / safe
    hue = np.where(
        value == red, np.where(towards_red < 0, towards_red + 6.0, towards_red),
        np.where(value == green, (blue - red) / safe + 2.0, (red - green) / safe + 4.0),
    ) * 60.0
    hue = np.where(coloured, hue, 0.0)
    # No spread means no saturation, which covers black: its value is 0 too.
    saturation = spread / np.where(coloured, value, 1.0)
    return hue, saturation, value


def luma(rgb: np.ndarray) -> np.ndarray:
    """Brightness as the eye weighs it, 0 to 255."""
    return rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian: high for crisp detail, low for blur."""
    g = gray.astype(np.float64)
    laplacian = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]
    return float(laplacian.var())


def clipped(gray: np.ndarray) -> float:
    """The share of pixels crushed to black or blown to white."""
    return float(((gray < 4) | (gray > 251)).mean())
