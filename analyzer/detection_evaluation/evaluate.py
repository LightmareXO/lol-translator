"""Aggregate interval metrics without committing local videos or full projects."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
from typing import Any


PRIMARY_MINIMUM_SECONDS = 1.2


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def intersection(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(
        0.0,
        min(left["end_seconds"], right["end_seconds"])
        - max(left["start_seconds"], right["start_seconds"]),
    )


def iou(left: dict[str, Any], right: dict[str, Any]) -> float:
    overlap = intersection(left, right)
    union = (
        left["end_seconds"]
        - left["start_seconds"]
        + right["end_seconds"]
        - right["start_seconds"]
        - overlap
    )
    return overlap / union if union > 0 else 0.0


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def evaluate_intervals(
    truth: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
    *,
    minimum_iou: float = 0.5,
) -> dict[str, Any]:
    candidates = sorted(
        (
            (iou(expected, actual), expected_index, actual_index)
            for expected_index, expected in enumerate(truth)
            for actual_index, actual in enumerate(predicted)
            if expected["line_id"] == actual["line_id"]
            and iou(expected, actual) >= minimum_iou
        ),
        reverse=True,
    )
    matched_truth: set[int] = set()
    matched_predicted: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _score, expected_index, actual_index in candidates:
        if expected_index in matched_truth or actual_index in matched_predicted:
            continue
        matched_truth.add(expected_index)
        matched_predicted.add(actual_index)
        matches.append((expected_index, actual_index))

    start_errors = [
        abs(truth[left]["start_seconds"] - predicted[right]["start_seconds"])
        for left, right in matches
    ]
    end_errors = [
        abs(truth[left]["end_seconds"] - predicted[right]["end_seconds"])
        for left, right in matches
    ]
    oversegmented = sum(
        max(
            0,
            sum(
                intersection(expected, actual) > 0
                for actual in predicted
                if actual["line_id"] == expected["line_id"]
            )
            - 1,
        )
        for expected in truth
    )
    merged = sum(
        max(
            0,
            sum(
                intersection(expected, actual) > 0
                for expected in truth
                if expected["line_id"] == actual["line_id"]
            )
            - 1,
        )
        for actual in predicted
    )
    match_count = len(matches)
    return {
        "truth_count": len(truth),
        "prediction_count": len(predicted),
        "matched_count": match_count,
        "precision": match_count / len(predicted) if predicted else 0.0,
        "recall": match_count / len(truth) if truth else 0.0,
        "oversegmentation_extra_count": oversegmented,
        "merged_truth_extra_count": merged,
        "start_error_median_seconds": statistics.median(start_errors) if start_errors else None,
        "start_error_p95_seconds": percentile(start_errors, 0.95),
        "end_error_median_seconds": statistics.median(end_errors) if end_errors else None,
        "end_error_p95_seconds": percentile(end_errors, 0.95),
        "unmatched_truth_ids": [
            item["id"] for index, item in enumerate(truth) if index not in matched_truth
        ],
    }


def project_intervals(project: dict[str, Any], *, legacy_line_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "line_id": item.get("line_id") or legacy_line_id,
            "start_seconds": item["start_seconds"],
            "end_seconds": item["end_seconds"],
        }
        for item in project["subtitles"]
    ]


def project_runtime(project: dict[str, Any], *, legacy_line_count: int) -> dict[str, Any]:
    processing = project["processing"]
    sample_count = processing["sample_count"]
    return {
        "sample_count": sample_count,
        "detected_interval_count": processing.get("detection_count", processing["subtitle_count"]),
        "ocr_call_count": processing.get("ocr_call_count", sample_count * legacy_line_count),
        "translation_call_count": processing.get(
            "translation_call_count", processing["subtitle_count"]
        ),
        "timings_ms": processing.get("timings_ms"),
        "peak_rss_bytes": processing.get("peak_rss_bytes"),
        "result_json_bytes": processing.get("result_json_bytes"),
        "error_count": len(processing.get("errors", [])),
    }


def parse_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, path = value.partition("=")
        if not separator or not key or not path:
            raise ValueError("result mappings must use PARTITION_ID=PATH")
        result[key] = Path(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--new-result", action="append", default=[])
    parser.add_argument("--legacy-result", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = read_json(args.manifest)
    annotation_document = read_json(args.annotations)
    annotations = annotation_document["events"]
    new_results = parse_mapping(args.new_result)
    legacy_results = parse_mapping(args.legacy_result)
    partitions = {item["id"]: item for item in manifest["partitions"]}
    output: dict[str, Any] = {
        "schema_version": 1,
        "manifest_frozen_at": manifest["frozen_at"],
        "annotation_review_status": annotation_document.get("review_status", "unknown"),
        "annotation_event_count": len(annotations),
        "primary_minimum_seconds": PRIMARY_MINIMUM_SECONDS,
        "partitions": {},
    }
    for partition_id in sorted(set(new_results) | set(legacy_results)):
        partition = partitions[partition_id]
        truth_all = [item for item in annotations if item["partition_id"] == partition_id]
        truth_primary = [
            item
            for item in truth_all
            if item["end_seconds"] - item["start_seconds"] + 1e-9
            >= PRIMARY_MINIMUM_SECONDS
        ]
        line_count = 2 if partition["line_split_ratio"] is not None else 1
        methods: dict[str, Any] = {}
        for method, mappings in (("image", new_results), ("legacy", legacy_results)):
            path = mappings.get(partition_id)
            if path is None:
                continue
            project = read_json(path)
            intervals = project_intervals(
                project,
                legacy_line_id="line-2" if line_count == 2 else "line-1",
            )
            methods[method] = {
                "all": evaluate_intervals(truth_all, intervals),
                "primary": evaluate_intervals(truth_primary, intervals),
                "runtime": project_runtime(project, legacy_line_count=line_count),
            }
        output["partitions"][partition_id] = {
            "role": partition["role"],
            "video_id": partition["video_id"],
            "annotation_count": len(truth_all),
            "primary_annotation_count": len(truth_primary),
            "methods": methods,
        }

    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
