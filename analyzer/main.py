"""Validate a local analysis request. OCR and translation are not run here."""

import argparse
import json
import math
from pathlib import Path
import sys


def read_request(request_path: Path) -> dict:
    with request_path.open(encoding="utf-8") as source:
        request = json.load(source)
    if not isinstance(request, dict) or set(request) != {"video_path", "subtitle_region"}:
        raise ValueError("Expected video_path and subtitle_region.")
    video_path = request["video_path"]
    if not isinstance(video_path, str) or not video_path.strip():
        raise ValueError("video_path must be a non-empty string.")
    video = Path(video_path)
    if not video.is_absolute() or not video.is_file():
        raise ValueError("video_path must point to an existing absolute file path.")
    region = request["subtitle_region"]
    if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
        raise ValueError("Expected x, y, width and height in subtitle_region.")
    for value in region.values():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Region coordinates must be finite numbers between 0 and 1.")
    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError("Region dimensions must be positive.")
    # Normalized floating-point arithmetic can differ by a few ULPs at the edge.
    if region["x"] + region["width"] > 1 + 1e-9 or region["y"] + region["height"] > 1 + 1e-9:
        raise ValueError("Region must fit inside the video.")
    return request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_path", type=Path)
    args = parser.parse_args()
    try:
        request = read_request(args.request_path)
    except (OSError, ValueError, OverflowError) as error:
        print(f"Invalid analysis request: {error}", file=sys.stderr)
        return 1
    print(json.dumps(request, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
