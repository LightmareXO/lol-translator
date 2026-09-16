"""Fixed image preprocessing shared by both OCR engines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import PREPROCESSING_MODES


def load_image(path: Path) -> Any:
    import cv2
    import numpy as np

    encoded = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not decode image: {path}")
    return image


def preprocess(image: Any, mode: str) -> Any:
    import cv2
    import numpy as np

    if mode not in PREPROCESSING_MODES:
        raise ValueError(f"unknown preprocessing mode: {mode}")
    if mode == "raw":
        return image
    if mode == "scale2x":
        return cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    if mode == "grayscale":
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    # Keep the midpoint fixed and increase contrast by 1.5x. This is applied to
    # every image, never selected per image based on its recognition result.
    return np.clip((image.astype(np.float32) - 127.5) * 1.5 + 127.5, 0, 255).astype(
        np.uint8
    )


PREPROCESSING_DESCRIPTION = {
    "raw": "OpenCV BGR decode only",
    "scale2x": "2x cubic interpolation (OpenCV INTER_CUBIC)",
    "grayscale": "OpenCV BGR to 8-bit grayscale, then equal-value 3-channel BGR",
    "contrast": "1.5x linear contrast around midpoint 127.5 with clipping",
}
