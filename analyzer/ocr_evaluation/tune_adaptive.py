"""Freeze one Adaptive threshold setting on the Issue #19 development subset."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.tesseract_pipeline import (  # type: ignore
        PaddleEngine,
        TesseractEngine,
        load_image,
        load_json,
        preprocess_image,
        recognize_case,
        score_records,
        write_json,
    )
else:
    from .tesseract_pipeline import (
        PaddleEngine,
        TesseractEngine,
        load_image,
        load_json,
        preprocess_image,
        recognize_case,
        score_records,
        write_json,
    )


def tune(
    spec_path: Path,
    prepared_path: Path,
    output_path: Path,
    paddle_model_directory: Path,
    tesseract: Path,
    fast_tessdata: Path,
    best_tessdata: Path,
    threads: int,
) -> dict[str, Any]:
    spec = load_json(spec_path)
    prepared = load_json(prepared_path)
    case_ids = set(spec["adaptive_tuning"]["development_case_ids"])
    cases = [case for case in prepared["cases"] if case["id"] in case_ids]
    if {case["id"] for case in cases} != case_ids:
        raise ValueError("adaptive tuning subset is missing prepared cases")
    engines = {
        "paddle": PaddleEngine(paddle_model_directory, threads),
        "tesseract_fast": TesseractEngine(
            tesseract, fast_tessdata, "fast", threads
        ),
        "tesseract_best": TesseractEngine(
            tesseract, best_tessdata, "best", threads
        ),
    }
    images_directory = prepared_path.parent / "images"
    candidates = []
    for candidate in spec["adaptive_tuning"]["candidates"]:
        engine_scores = {}
        raw_records = []
        for engine_key, engine in engines.items():
            records = []
            for case in cases:
                raw_output = ""
                error = None
                elapsed_ms = None
                try:
                    image = load_image(images_directory / case["image_file"])
                    prepared_image = preprocess_image(
                        image,
                        "adaptive",
                        adaptive_block_size=candidate["block_size"],
                        adaptive_c=candidate["c"],
                    )
                    raw_output, elapsed_ms = recognize_case(
                        engine, prepared_image, case
                    )
                except Exception as exception:
                    error = f"{type(exception).__name__}: {exception}"
                record = {
                    "case_id": case["id"],
                    "ground_truth": case["ground_truth"],
                    "raw_output": raw_output,
                    "inference_ms": elapsed_ms,
                    "total_ms": elapsed_ms,
                    "error": error,
                }
                records.append(record)
                raw_records.append({"engine_key": engine_key, **record})
            engine_scores[engine_key] = score_records(records)
        mean_cer = sum(
            score["whitespace_free_cer"] for score in engine_scores.values()
        ) / len(engine_scores)
        candidates.append(
            {
                **candidate,
                "mean_engine_whitespace_free_cer": mean_cer,
                "engine_scores": engine_scores,
                "records": raw_records,
            }
        )
    selected = min(
        candidates,
        key=lambda item: (
            item["mean_engine_whitespace_free_cer"],
            item["block_size"],
            item["c"],
        ),
    )
    result = {
        "schema_version": 1,
        "split": "development_reused",
        "reference_status": "ai_visual_transcription_provisional",
        "independent_evaluation": False,
        "case_ids": sorted(case_ids),
        "selection_rule": spec["adaptive_tuning"]["selection_rule"],
        "candidates": candidates,
        "selected": {
            "block_size": selected["block_size"],
            "c": selected["c"],
            "polarity": selected["polarity"],
            "mean_engine_whitespace_free_cer": selected[
                "mean_engine_whitespace_free_cer"
            ],
        },
    }
    write_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paddle-model-dir", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, required=True)
    parser.add_argument("--fast-tessdata", type=Path, required=True)
    parser.add_argument("--best-tessdata", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    try:
        tune(
            args.spec,
            args.prepared,
            args.output,
            args.paddle_model_dir,
            args.tesseract,
            args.fast_tessdata,
            args.best_tessdata,
            args.threads,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Adaptive tuning failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
