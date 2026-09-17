"""Pure contracts and timeline operations for the runtime analyzer."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable


SCHEMA_VERSION = 1
PROJECT_KIND = "lol-translator-project"
DEFAULT_SAMPLE_INTERVAL_MS = 200
MIN_SAMPLE_INTERVAL_MS = 50
MAX_SAMPLE_INTERVAL_MS = 5_000


def atomic_write_json(path: Path, value: Any) -> None:
    """Write UTF-8 JSON without exposing a partially-written destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_for_comparison(text: str) -> str:
    """Ignore Unicode form and whitespace only; preserve every other character."""
    normalized = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", "", normalized)


def validate_region(region: Any) -> dict[str, float]:
    expected = {"x", "y", "width", "height"}
    if not isinstance(region, dict) or set(region) != expected:
        raise ValueError("subtitle_region must contain x, y, width and height")
    checked: dict[str, float] = {}
    for key in expected:
        value = region[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("subtitle_region values must be finite numbers")
        checked[key] = float(value)
    if (
        checked["x"] < 0
        or checked["y"] < 0
        or checked["width"] <= 0
        or checked["height"] <= 0
        or checked["x"] + checked["width"] > 1 + 1e-9
        or checked["y"] + checked["height"] > 1 + 1e-9
    ):
        raise ValueError("subtitle_region must fit inside the video")
    return checked


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("analysis request must be an object")
    expected = {
        "schema_version",
        "video_path",
        "subtitle_region",
        "analysis_range",
        "settings",
    }
    if set(value) != expected:
        raise ValueError(f"analysis request fields must be {sorted(expected)}")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {value['schema_version']!r}")

    video_path = value["video_path"]
    if not isinstance(video_path, str) or not video_path.strip():
        raise ValueError("video_path must be a non-empty string")
    video = Path(video_path)
    if not video.is_absolute() or not video.is_file():
        raise ValueError("video_path must point to an existing absolute file")
    region = validate_region(value["subtitle_region"])

    analysis_range = value["analysis_range"]
    if not isinstance(analysis_range, dict) or set(analysis_range) != {
        "mode",
        "start_seconds",
        "end_seconds",
    }:
        raise ValueError("analysis_range has an invalid shape")
    mode = analysis_range["mode"]
    if mode not in ("range", "whole"):
        raise ValueError("analysis_range.mode must be range or whole")
    start = analysis_range["start_seconds"]
    end = analysis_range["end_seconds"]
    if mode == "range":
        if type(start) not in (int, float) or type(end) not in (int, float):
            raise ValueError("range start and end must be numbers")
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("range must have finite 0 <= start < end values")
    elif start is not None or end is not None:
        raise ValueError("whole-video analysis must use null start and end")

    settings = value["settings"]
    if not isinstance(settings, dict) or set(settings) != {
        "sample_interval_ms",
        "line_split_ratio",
    }:
        raise ValueError("settings has an invalid shape")
    interval = settings["sample_interval_ms"]
    if type(interval) is not int or not MIN_SAMPLE_INTERVAL_MS <= interval <= MAX_SAMPLE_INTERVAL_MS:
        raise ValueError(
            f"sample_interval_ms must be an integer from {MIN_SAMPLE_INTERVAL_MS} to {MAX_SAMPLE_INTERVAL_MS}"
        )
    split = settings["line_split_ratio"]
    if split is not None and (
        type(split) not in (int, float) or not math.isfinite(split) or not 0.1 <= split <= 0.9
    ):
        raise ValueError("line_split_ratio must be null or a number from 0.1 to 0.9")

    return {
        "schema_version": SCHEMA_VERSION,
        "video_path": str(video),
        "subtitle_region": region,
        "analysis_range": {
            "mode": mode,
            "start_seconds": None if mode == "whole" else float(start),
            "end_seconds": None if mode == "whole" else float(end),
        },
        "settings": {
            "sample_interval_ms": interval,
            "line_split_ratio": None if split is None else float(split),
        },
    }


def resolve_analysis_range(request: dict[str, Any], duration_seconds: float) -> tuple[float, float]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("video duration could not be determined")
    selection = request["analysis_range"]
    if selection["mode"] == "whole":
        return 0.0, duration_seconds
    start = float(selection["start_seconds"])
    end = float(selection["end_seconds"])
    if start >= duration_seconds:
        raise ValueError("analysis start is outside the video")
    if end > duration_seconds + 0.05:
        raise ValueError("analysis end is outside the video")
    return start, min(end, duration_seconds)


@dataclass(frozen=True)
class Sample:
    timestamp_seconds: float
    status: str
    raw_text: str = ""
    raw_lines: tuple[str, ...] = ()
    confidence: float | None = None
    image_png_base64: str | None = None
    error: str | None = None


def merge_samples(
    samples: Iterable[Sample], *, interval_seconds: float, range_end_seconds: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge only adjacent samples with exactly equal whitespace-normalized text."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    subtitles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def close_current(end: float) -> None:
        nonlocal current
        if current is None:
            return
        current["end_seconds"] = max(current["start_seconds"], min(end, range_end_seconds))
        current["id"] = f"subtitle-{len(subtitles) + 1:05d}"
        subtitles.append(current)
        current = None

    previous_timestamp: float | None = None
    for sample in samples:
        if previous_timestamp is not None and sample.timestamp_seconds < previous_timestamp:
            raise ValueError("samples must be sorted by timestamp")
        previous_timestamp = sample.timestamp_seconds
        if sample.status not in ("subtitle", "no_subtitle", "ocr_error"):
            raise ValueError(f"unknown sample status: {sample.status}")
        if sample.status == "ocr_error":
            close_current(sample.timestamp_seconds)
            errors.append(
                {
                    "phase": "ocr",
                    "timestamp_seconds": sample.timestamp_seconds,
                    "message": sample.error or "OCR failed",
                }
            )
            continue
        normalized = normalize_for_comparison(sample.raw_text)
        if sample.status == "no_subtitle" or not normalized:
            close_current(sample.timestamp_seconds)
            continue
        if current is not None and current["comparison_text"] == normalized:
            current["last_sample_seconds"] = sample.timestamp_seconds
            continue
        close_current(sample.timestamp_seconds)
        current = {
            "id": "",
            "start_seconds": sample.timestamp_seconds,
            "end_seconds": sample.timestamp_seconds,
            "last_sample_seconds": sample.timestamp_seconds,
            "comparison_text": normalized,
            "image_png_base64": sample.image_png_base64,
            "ocr": {
                "status": "completed",
                "raw_text": sample.raw_text,
                "raw_lines": list(sample.raw_lines or (sample.raw_text,)),
                "confidence": sample.confidence,
                "error": None,
            },
            "corrected_ko": None,
            "translation": {
                "status": "pending",
                "source_ko": sample.raw_text,
                "generated_ja": None,
                "user_ja": None,
                "error": None,
            },
        }

    if current is not None:
        close_current(min(current["last_sample_seconds"] + interval_seconds, range_end_seconds))
    for subtitle in subtitles:
        subtitle.pop("comparison_text", None)
        subtitle.pop("last_sample_seconds", None)
    return subtitles, errors


def effective_korean(subtitle: dict[str, Any]) -> str:
    corrected = subtitle.get("corrected_ko")
    return corrected if isinstance(corrected, str) and corrected.strip() else subtitle["ocr"]["raw_text"]


def apply_translation(
    subtitle: dict[str, Any], generated_ja: str, *, source_ko: str | None = None
) -> None:
    """Update generated output while preserving any explicit Japanese edit."""
    if not isinstance(generated_ja, str) or not generated_ja.strip():
        raise ValueError("generated translation must not be empty")
    translation = subtitle["translation"]
    translation["generated_ja"] = generated_ja.strip()
    translation["source_ko"] = source_ko or effective_korean(subtitle)
    translation["status"] = "completed"
    translation["error"] = None


def mark_translation_stale(subtitle: dict[str, Any], corrected_ko: str | None) -> None:
    subtitle["corrected_ko"] = corrected_ko if corrected_ko and corrected_ko.strip() else None
    translation = subtitle["translation"]
    if translation.get("source_ko") != effective_korean(subtitle):
        translation["status"] = "stale"
    elif translation.get("status") == "stale":
        translation["status"] = "completed" if translation.get("generated_ja") else "pending"


def validate_project(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("project must be an object")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != PROJECT_KIND:
        raise ValueError("unsupported project schema")
    if not isinstance(value.get("subtitles"), list):
        raise ValueError("project subtitles must be a list")
    previous_end = -1.0
    seen_ids: set[str] = set()
    for subtitle in value["subtitles"]:
        if not isinstance(subtitle, dict):
            raise ValueError("subtitle must be an object")
        identifier = subtitle.get("id")
        start = subtitle.get("start_seconds")
        end = subtitle.get("end_seconds")
        if not isinstance(identifier, str) or not identifier or identifier in seen_ids:
            raise ValueError("subtitle ids must be unique non-empty strings")
        if type(start) not in (int, float) or type(end) not in (int, float):
            raise ValueError("subtitle timestamps must be numbers")
        if not math.isfinite(start) or not math.isfinite(end) or start < previous_end or end < start:
            raise ValueError("subtitle timestamps must be ordered and finite")
        ocr = subtitle.get("ocr")
        translation = subtitle.get("translation")
        if not isinstance(ocr, dict) or not isinstance(ocr.get("raw_text"), str):
            raise ValueError("subtitle OCR raw output is missing")
        if not isinstance(translation, dict):
            raise ValueError("subtitle translation is missing")
        user_ja = translation.get("user_ja")
        if user_ja is not None and not isinstance(user_ja, str):
            raise ValueError("user_ja must be null or a string")
        seen_ids.add(identifier)
        previous_end = float(end)
    return value
