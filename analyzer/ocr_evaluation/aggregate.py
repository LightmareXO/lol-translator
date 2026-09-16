"""Validate OCR runs and generate shareable detailed and summary results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.core import (  # type: ignore
        PREPROCESSING_MODES,
        edit_distance,
        load_manifest,
        normalize_text,
        remove_whitespace,
        summarize_records,
    )
else:
    from .core import (
        PREPROCESSING_MODES,
        edit_distance,
        load_manifest,
        normalize_text,
        remove_whitespace,
        summarize_records,
    )


def manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def load_result(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        result = json.load(source)
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported result schema")
    if not isinstance(result.get("metadata"), dict) or not isinstance(
        result.get("records"), list
    ):
        raise ValueError(f"{path}: metadata and records are required")
    return result


def validate_result(
    result: dict[str, Any], path: Path, manifest: dict[str, Any], digest: str
) -> None:
    if result.get("manifest_sha256") != digest:
        raise ValueError(f"{path}: result was produced from a different manifest")
    metadata = result["metadata"]
    if metadata.get("preprocessing") not in PREPROCESSING_MODES:
        raise ValueError(f"{path}: unknown preprocessing mode")
    expected_ids = [item["id"] for item in manifest["images"]]
    actual_ids = [record.get("image_id") for record in result["records"]]
    if actual_ids != expected_ids:
        raise ValueError(f"{path}: records must match manifest image order exactly")
    for item, record in zip(manifest["images"], result["records"]):
        for key in (
            "group_id",
            "timestamp_seconds",
            "tags",
            "line_count",
            "ground_truth",
        ):
            if record.get(key) != item[key]:
                raise ValueError(f"{path}: {item['id']} has mismatched {key}")


def configuration_summary(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result["metadata"]
    records = result["records"]
    summary = summarize_records(records)
    tags = sorted({tag for record in records for tag in record["tags"]})
    accuracy_keys = (
        "image_count",
        "failure_count",
        "reference_characters",
        "edit_distance",
        "cer",
        "exact_matches",
        "whitespace_free_cer",
    )
    summary["by_tag"] = {}
    for tag in tags:
        tag_summary = summarize_records(
            [record for record in records if tag in record["tags"]]
        )
        summary["by_tag"][tag] = {key: tag_summary[key] for key in accuracy_keys}
    summary.update(
        {
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "model_id": metadata["model_id"],
            "preprocessing": metadata["preprocessing"],
            "initialization_ms": metadata["initialization_ms"],
            "first_warmup_prediction_ms": metadata["first_warmup_prediction_ms"],
            "script_entry_to_first_prediction_ms": metadata[
                "script_entry_to_first_prediction_ms"
            ],
            "peak_parent_rss_bytes": metadata["peak_parent_rss_bytes"],
            "peak_process_tree_rss_bytes": metadata["peak_process_tree_rss_bytes"],
            "model_directory_bytes": metadata["model_directory_bytes"],
            "python_environment_bytes": metadata["python_environment_bytes"],
            "threads": metadata["threads"],
            "warmup_count": metadata["warmup_count"],
        }
    )
    return summary


def dataset_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    groups = {item["group_id"] for item in manifest["images"]}
    tags = sorted({tag for item in manifest["images"] for tag in item["tags"]})
    return {
        "image_count": len(manifest["images"]),
        "subtitle_group_count": len(groups),
        "verified_ground_truth_count": sum(
            item["ground_truth_status"] == "verified" for item in manifest["images"]
        ),
        "conditions": {
            tag: {
                "image_count": sum(tag in item["tags"] for item in manifest["images"]),
                "subtitle_group_count": len(
                    {
                        item["group_id"]
                        for item in manifest["images"]
                        if tag in item["tags"]
                    }
                ),
            }
            for tag in tags
        },
    }


def detailed_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = result["metadata"]
    rows = []
    for record in result["records"]:
        truth = normalize_text(record["ground_truth"])
        prediction = normalize_text(record.get("raw_output") or "")
        truth_without_whitespace = remove_whitespace(truth)
        prediction_without_whitespace = remove_whitespace(prediction)
        rows.append(
            {
                "engine": metadata["engine"],
                "engine_version": metadata["engine_version"],
                "model_id": metadata["model_id"],
                "preprocessing": metadata["preprocessing"],
                "image_id": record["image_id"],
                "group_id": record["group_id"],
                "timestamp_seconds": record["timestamp_seconds"],
                "tags": "|".join(record["tags"]),
                "line_count": record["line_count"],
                "ground_truth": record["ground_truth"],
                "normalized_ground_truth": truth,
                "raw_output": record.get("raw_output") or "",
                "normalized_output": prediction,
                "edit_distance": edit_distance(truth, prediction),
                "reference_characters": len(truth),
                "exact_match": truth == prediction,
                "whitespace_free_edit_distance": edit_distance(
                    truth_without_whitespace, prediction_without_whitespace
                ),
                "whitespace_free_reference_characters": len(truth_without_whitespace),
                "confidence": record.get("confidence"),
                "preprocessing_ms": record.get("preprocessing_ms"),
                "inference_ms": record.get("inference_ms"),
                "total_ms": record.get("total_ms"),
                "error": record.get("error") or "",
            }
        )
    return rows


def write_markdown(summary: dict[str, Any], path: Path) -> None:
    def decimal(value: float | None, places: int) -> str:
        return "n/a" if value is None else f"{value:.{places}f}"

    lines = [
        "# OCR比較の自動集計",
        "",
        "主評価はUnicode NFC後も空白・句読点・改行を保持したCERです。",
        "速度は画像読込を除くOCR推論と、画像読込・固定前処理を含む合計を分けています。",
        "",
        "| エンジン | モデル | 前処理 | CER | 完全一致 | 推論中央値 (ms) | 推論p95 (ms) | 揺れ率 | Peak RSS (MiB) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for configuration in sorted(
        summary["configurations"], key=lambda item: (item["engine"], item["preprocessing"])
    ):
        lines.append(
            "| {engine} | {model_id} | {preprocessing} | {cer} | "
            "{exact_matches}/{image_count} | {median} | {p95} | {change} | {memory:.1f} |".format(
                engine=configuration["engine"],
                model_id=configuration["model_id"],
                preprocessing=configuration["preprocessing"],
                cer=decimal(configuration["cer"], 4),
                exact_matches=configuration["exact_matches"],
                image_count=configuration["image_count"],
                median=decimal(configuration["inference_ms_median"], 1),
                p95=decimal(configuration["inference_ms_p95"], 1),
                change=decimal(
                    configuration["stability"]["group_mean_change_rate"], 4
                ),
                memory=configuration["peak_process_tree_rss_bytes"] / 1024 / 1024,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def aggregate(
    manifest_path: Path, result_paths: list[Path], output_directory: Path
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    digest = manifest_digest(manifest_path)
    results = []
    seen_configurations: set[tuple[str, str]] = set()
    for path in result_paths:
        result = load_result(path)
        validate_result(result, path, manifest, digest)
        key = (result["metadata"]["engine"], result["metadata"]["preprocessing"])
        if key in seen_configurations:
            raise ValueError(f"duplicate configuration: {key[0]} {key[1]}")
        seen_configurations.add(key)
        results.append(result)
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = [row for result in results for row in detailed_rows(result)]
    if not rows:
        raise ValueError("at least one result is required")
    with (output_directory / "details.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "manifest_sha256": digest,
        "dataset": dataset_summary(manifest),
        "evaluation": {
            "primary_metric": "corpus CER after Unicode NFC; whitespace, punctuation and newlines preserved",
            "character_unit": "Unicode code point after NFC (precomposed Hangul syllables remain one code point)",
            "auxiliary_metric": "CER with all Unicode whitespace removed",
            "percentile_method": "linear interpolation at (n - 1) * p",
            "failed_inferences": "included as empty predictions in accuracy metrics",
        },
        "configurations": [configuration_summary(result) for result in results],
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(summary, output_directory / "comparison.md")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        aggregate(args.manifest, args.results, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"Aggregation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
