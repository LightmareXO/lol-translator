"""Shared validation and metrics for the OCR evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata
from typing import Any, Iterable


PREPROCESSING_MODES = ("raw", "scale2x", "grayscale", "contrast")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        manifest = json.load(source)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if manifest.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    for key in ("video_id", "sha256", "width", "height", "fps", "subtitle_region"):
        if key not in source:
            raise ValueError(f"source.{key} is required")
    if not isinstance(source["video_id"], str) or not source["video_id"]:
        raise ValueError("source.video_id must be a non-empty string")
    if not isinstance(source["sha256"], str) or not SHA256_PATTERN.fullmatch(
        source["sha256"]
    ):
        raise ValueError("source.sha256 must be a lowercase SHA-256 digest")
    for key in ("width", "height"):
        if type(source[key]) is not int or source[key] <= 0:
            raise ValueError(f"source.{key} must be a positive integer")
    normalized_region_to_pixels(
        source["subtitle_region"], source["width"], source["height"]
    )

    extraction = manifest.get("extraction")
    if not isinstance(extraction, dict) or extraction.get("tool") != "ffmpeg":
        raise ValueError("extraction.tool must be ffmpeg")

    images = manifest.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError("images must be a non-empty array")
    image_ids: set[str] = set()
    image_files: set[str] = set()
    group_truth: dict[str, str] = {}
    for index, item in enumerate(images):
        prefix = f"images[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{prefix} must be an object")
        for key in (
            "id",
            "group_id",
            "timestamp_seconds",
            "image_file",
            "ground_truth",
            "ground_truth_status",
            "line_count",
            "tags",
        ):
            if key not in item:
                raise ValueError(f"{prefix}.{key} is required")
        if not isinstance(item["id"], str) or not item["id"]:
            raise ValueError(f"{prefix}.id must be a non-empty string")
        if item["id"] in image_ids:
            raise ValueError(f"duplicate image id: {item['id']}")
        image_ids.add(item["id"])
        if not isinstance(item["group_id"], str) or not item["group_id"]:
            raise ValueError(f"{prefix}.group_id must be a non-empty string")
        timestamp = item["timestamp_seconds"]
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError(f"{prefix}.timestamp_seconds must be finite and non-negative")
        if not isinstance(item["image_file"], str) or not item["image_file"]:
            raise ValueError(f"{prefix}.image_file must be a non-empty string")
        image_file = Path(item["image_file"])
        if image_file.is_absolute() or image_file.name != item["image_file"]:
            raise ValueError(f"{prefix}.image_file must be a plain file name")
        if item["image_file"] in image_files:
            raise ValueError(f"duplicate image file: {item['image_file']}")
        image_files.add(item["image_file"])
        truth = item["ground_truth"]
        if not isinstance(truth, str) or not truth:
            raise ValueError(f"{prefix}.ground_truth must be a non-empty string")
        if item["ground_truth_status"] != "verified":
            raise ValueError(f"{prefix}.ground_truth_status must be verified")
        line_count = item["line_count"]
        if type(line_count) is not int or line_count != truth.count("\n") + 1:
            raise ValueError(f"{prefix}.line_count does not match ground_truth")
        tags = item["tags"]
        if (
            not isinstance(tags, list)
            or not tags
            or any(not isinstance(tag, str) or not tag for tag in tags)
            or len(tags) != len(set(tags))
        ):
            raise ValueError(f"{prefix}.tags must contain unique non-empty strings")
        prior_truth = group_truth.setdefault(item["group_id"], truth)
        if prior_truth != truth:
            raise ValueError(f"group {item['group_id']} has inconsistent ground truth")


def normalized_region_to_pixels(
    region: Any, frame_width: int, frame_height: int
) -> tuple[int, int, int, int]:
    if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
        raise ValueError("subtitle_region must contain x, y, width and height")
    values = [region[key] for key in ("x", "y", "width", "height")]
    if any(
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
        for value in values
    ):
        raise ValueError("subtitle_region values must be finite numbers from 0 to 1")
    x, y, width, height = values
    if width <= 0 or height <= 0 or x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
        raise ValueError("subtitle_region must have positive area inside the frame")

    def round_half_up(value: float) -> int:
        return int(math.floor(value + 0.5))

    left = round_half_up(x * frame_width)
    top = round_half_up(y * frame_height)
    right = round_half_up((x + width) * frame_width)
    bottom = round_half_up((y + height) * frame_height)
    if right <= left or bottom <= top:
        raise ValueError("subtitle_region rounds to an empty pixel rectangle")
    return left, top, right - left, bottom - top


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    """Normalize canonical Unicode only; spaces, punctuation and newlines remain."""
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def remove_whitespace(text: str) -> str:
    return "".join(character for character in text if not character.isspace())


def edit_distance(reference: str, prediction: str) -> int:
    previous = list(range(len(prediction) + 1))
    for row_index, reference_character in enumerate(reference, start=1):
        current = [row_index]
        for column_index, prediction_character in enumerate(prediction, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1]
                    + (reference_character != prediction_character),
                )
            )
        previous = current
    return previous[-1]


def percentile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile needs at least one value")
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("records must not be empty")
    reference_characters = 0
    whitespace_free_characters = 0
    errors = 0
    whitespace_free_errors = 0
    exact_matches = 0
    failures = 0
    inference_times: list[float] = []
    total_times: list[float] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        truth = normalize_text(record["ground_truth"])
        prediction = normalize_text(record.get("raw_output") or "")
        reference_characters += len(truth)
        errors += edit_distance(truth, prediction)
        truth_without_whitespace = remove_whitespace(truth)
        prediction_without_whitespace = remove_whitespace(prediction)
        whitespace_free_characters += len(truth_without_whitespace)
        whitespace_free_errors += edit_distance(
            truth_without_whitespace, prediction_without_whitespace
        )
        exact_matches += truth == prediction
        failures += bool(record.get("error"))
        if record.get("inference_ms") is not None:
            inference_times.append(float(record["inference_ms"]))
        if record.get("total_ms") is not None:
            total_times.append(float(record["total_ms"]))
        groups.setdefault(record["group_id"], []).append(record)

    stability_groups: list[dict[str, Any]] = []
    for group_id, group_records in sorted(groups.items()):
        ordered = sorted(group_records, key=lambda record: record["timestamp_seconds"])
        pair_distances: list[float] = []
        changed = 0
        for left, right in zip(ordered, ordered[1:]):
            left_text = normalize_text(left.get("raw_output") or "")
            right_text = normalize_text(right.get("raw_output") or "")
            changed += left_text != right_text
            pair_distances.append(
                edit_distance(left_text, right_text)
                / max(len(left_text), len(right_text), 1)
            )
        pair_count = len(pair_distances)
        stability_groups.append(
            {
                "group_id": group_id,
                "adjacent_pairs": pair_count,
                "changed_pairs": changed,
                "change_rate": changed / pair_count if pair_count else None,
                "mean_normalized_edit_distance": (
                    statistics.fmean(pair_distances) if pair_distances else None
                ),
                "exact_matches": sum(
                    normalize_text(record["ground_truth"])
                    == normalize_text(record.get("raw_output") or "")
                    for record in ordered
                ),
            }
        )
    comparable_groups = [group for group in stability_groups if group["adjacent_pairs"]]
    return {
        "image_count": len(records),
        "failure_count": failures,
        "reference_characters": reference_characters,
        "edit_distance": errors,
        "cer": errors / reference_characters if reference_characters else None,
        "exact_matches": exact_matches,
        "whitespace_free_cer": (
            whitespace_free_errors / whitespace_free_characters
            if whitespace_free_characters
            else None
        ),
        "inference_ms_median": (
            statistics.median(inference_times) if inference_times else None
        ),
        "inference_ms_p95": percentile(inference_times, 0.95) if inference_times else None,
        "total_ms_median": statistics.median(total_times) if total_times else None,
        "total_ms_p95": percentile(total_times, 0.95) if total_times else None,
        "stability": {
            "groups": stability_groups,
            "group_mean_change_rate": (
                statistics.fmean(group["change_rate"] for group in comparable_groups)
                if comparable_groups
                else None
            ),
            "group_mean_normalized_edit_distance": (
                statistics.fmean(
                    group["mean_normalized_edit_distance"] for group in comparable_groups
                )
                if comparable_groups
                else None
            ),
        },
    }
