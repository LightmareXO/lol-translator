"""Aggregate the fixed Tesseract.js 5.1.1 browser-equivalent reference condition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .tesseract_pipeline import (
    load_image,
    load_json,
    score_records,
    sha256_file,
    stability_summary,
    write_json,
)


LOCAL_ENGINE_KEYS = ("tesseract_fast", "tesseract_best")
MAPPING_FIELDS = (
    "group_id",
    "video_id",
    "timestamp_seconds",
    "image_file",
    "image_sha256",
    "ground_truth",
    "reference_status",
    "split",
    "representative",
    "line_count",
)


def _configuration(
    summary: dict[str, Any], engine_key: str, preprocessing: str
) -> dict[str, Any]:
    matches = [
        item
        for item in summary["configurations"]
        if item["engine_key"] == engine_key
        and item["preprocessing"] == preprocessing
    ]
    if len(matches) != 1:
        raise ValueError(
            f"比較対象が一意ではありません: {engine_key}/{preprocessing}"
        )
    return matches[0]


def aggregate_reference(
    reference: dict[str, Any],
    local_summary: dict[str, Any],
    local_details: list[dict[str, Any]],
    prepared: dict[str, Any],
    source_images: Path,
    local_original_images: Path,
) -> dict[str, Any]:
    condition = reference["condition"]
    expected = {
        "tesseract_js": "5.1.1",
        "languages": "kor",
        "oem": 1,
        "psm": 6,
        "whole_roi": True,
        "external_preprocessing": "none",
        "line_splitting": "none",
    }
    for key, value in expected.items():
        if condition.get(key) != value:
            raise ValueError(f"参照条件が一致しません: {key}")
    if reference["prepared_sha256"] != local_summary["prepared_sha256"]:
        raise ValueError("Tesseract.jsとローカル比較のprepared.jsonが一致しません")

    records = reference["records"]
    representative = [item for item in records if item["representative"]]
    if len(records) != 28 or len(representative) != 12:
        raise ValueError("固定済み28画像・代表12字幕と一致しません")

    prepared_by_id = {item["id"]: item for item in prepared["cases"]}
    reference_by_id = {item["id"]: item for item in records}
    if set(prepared_by_id) != set(reference_by_id):
        raise ValueError("prepared.jsonとTesseract.js出力のID集合が一致しません")
    for case_id, expected in prepared_by_id.items():
        actual = reference_by_id[case_id]
        for field in MAPPING_FIELDS:
            if actual[field] != expected[field]:
                raise ValueError(f"Tesseract.js出力の対応がずれています: {case_id}/{field}")

    local_configurations = {
        engine_key: _configuration(local_summary, engine_key, "original")
        for engine_key in LOCAL_ENGINE_KEYS
    }
    local_original = {
        (item["engine_key"], item["id"]): item
        for item in local_details
        if item["engine_key"] in LOCAL_ENGINE_KEYS
        and item["preprocessing"] == "original"
        and item["representative"]
    }
    if len(local_original) != 24:
        raise ValueError("ローカル原画像条件の代表出力が12件×2方式ではありません")
    local_original_all = [
        item
        for item in local_details
        if item["engine_key"] in LOCAL_ENGINE_KEYS
        and item["preprocessing"] == "original"
    ]
    for item in local_original_all:
        expected = prepared_by_id[item["id"]]
        for field in MAPPING_FIELDS:
            if item[field] != expected[field]:
                raise ValueError(
                    f"ローカル出力の対応がずれています: {item['engine_key']}/{item['id']}/{field}"
                )

    import numpy as np

    pixel_identical_count = 0
    matching_shape_count = 0
    for item in prepared["cases"]:
        source_path = source_images / item["image_file"]
        local_path = local_original_images / item["image_file"]
        if sha256_file(source_path) != item["image_sha256"]:
            raise ValueError(f"抽出元画像のSHA-256が一致しません: {item['id']}")
        source_image = load_image(source_path)
        local_image = load_image(local_path)
        matching_shape_count += source_image.shape == local_image.shape
        pixel_identical_count += bool(np.array_equal(source_image, local_image))
    if matching_shape_count != 28 or pixel_identical_count != 28:
        raise ValueError("Tesseract.jsとローカル条件の入力画素が一致しません")

    reference_score = score_records(representative)
    best_local_cer = min(
        item["representative"]["cer"] for item in local_configurations.values()
    )
    improved = reference_score["cer"] < best_local_cer
    compact_reference_characters = sum(
        len("".join(item["ground_truth"].split())) for item in representative
    )
    compact_prediction_characters = sum(
        len("".join(item["raw_output"].split())) for item in representative
    )
    comparisons = []
    for item in representative:
        comparisons.append(
            {
                "id": item["id"],
                "video_id": item["video_id"],
                "timestamp_seconds": item["timestamp_seconds"],
                "line_count": item["line_count"],
                "ground_truth": item["ground_truth"],
                "tesseract_js_kor_psm6": item["raw_output"],
                "local_fast_kor_eng_psm7": local_original[
                    ("tesseract_fast", item["id"])
                ]["raw_output"],
                "local_best_kor_eng_psm7": local_original[
                    ("tesseract_best", item["id"])
                ]["raw_output"],
            }
        )

    return {
        "schema_version": 1,
        "scope": "Issue #19 Tesseract.js 5.1.1 browser-equivalent reference",
        "reference_status": "ai_visual_transcription_provisional",
        "human_confirmed_subtitles": 0,
        "independent_evaluation": False,
        "prepared_sha256": reference["prepared_sha256"],
        "integrity_audit": {
            "same_roi": {
                "checked_images": 28,
                "matching_dimensions": matching_shape_count,
                "pixel_identical_images": pixel_identical_count,
                "maximum_pixel_difference": 0,
            },
            "record_mapping": {
                "checked_fields": list(MAPPING_FIELDS),
                "tesseract_js_records": len(records),
                "local_original_records": len(local_original_all),
                "mismatch_count": 0,
            },
            "background_text_indicator": {
                "representative_images": len(representative),
                "compact_reference_characters": compact_reference_characters,
                "compact_prediction_characters": compact_prediction_characters,
                "cases_prediction_longer_than_reference": sum(
                    len("".join(item["raw_output"].split()))
                    > len("".join(item["ground_truth"].split()))
                    for item in representative
                ),
                "multiline_outputs": sum("\n" in item["raw_output"] for item in representative),
                "interpretation": "全12件で正解文より長く、広いROI内の背景・HUDを文字として認識した出力を目視確認した。",
            },
            "error_separation": {
                "failed_records": sum(bool(item.get("error")) for item in records),
                "records_with_error_and_text": sum(
                    bool(item.get("error")) and bool(item.get("raw_output"))
                    for item in records
                ),
                "raw_output_source": "Tesseract.js recognize() result.data.text only; exceptions are stored in error",
            },
        },
        "reference_condition": condition,
        "toolchain": reference["metadata"],
        "reference_scores": {
            "representative_12": reference_score,
            "all_28_adjacent_frames": score_records(records),
            "adjacent_frame_stability": stability_summary(records),
        },
        "current_local_original_conditions": {
            engine_key: {
                "languages": "kor+eng",
                "oem": 1,
                "one_line_psm": 7,
                "two_line_policy": "fixed line bands, PSM 7 per line",
                "external_preprocessing": "none",
                "scores": configuration["representative"],
            }
            for engine_key, configuration in local_configurations.items()
        },
        "improvement_gate": {
            "criterion": "reference CER is lower than the best current local original-input CER",
            "reference_cer": reference_score["cer"],
            "best_current_local_cer": best_local_cer,
            "improved": improved,
            "ablation_executed": False,
            "reason": (
                "Web相当条件の改善を確認できなかったため、言語指定・PSM・モデルの切り分けは実行しない。"
                if not improved
                else "改善を確認したため、切り分け実験が必要。"
            ),
        },
        "records": records,
        "representative_output_comparison": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--local-summary", type=Path, required=True)
    parser.add_argument("--local-details", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--package-lock", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--source-images", type=Path, required=True)
    parser.add_argument("--local-original-images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    local_details = json.loads(args.local_details.read_text(encoding="utf-8"))
    if not isinstance(local_details, list):
        raise ValueError("local details must contain a JSON array")
    result = aggregate_reference(
        load_json(args.reference),
        load_json(args.local_summary),
        local_details,
        load_json(args.prepared),
        args.source_images,
        args.local_original_images,
    )
    result["reproduction_files"] = {
        "runner_sha256": sha256_file(args.runner),
        "package_lock_sha256": sha256_file(args.package_lock),
    }
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
