"""Limited reuse of PR #12 background suppression outputs for Issue #19."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.tesseract_pipeline import (  # type: ignore
        PaddleEngine,
        PREPROCESSING_MODES,
        TesseractEngine,
        load_image,
        load_json,
        preprocess_image,
        recognize_case,
        score_records,
        sha256_file,
        write_json,
    )
else:
    from .tesseract_pipeline import (
        PaddleEngine,
        PREPROCESSING_MODES,
        TesseractEngine,
        load_image,
        load_json,
        preprocess_image,
        recognize_case,
        score_records,
        sha256_file,
        write_json,
    )


def chosen_modes(summary: dict[str, Any]) -> dict[str, str]:
    selected: dict[str, tuple[float, int, str]] = {}
    for configuration in summary["configurations"]:
        key = configuration["engine_key"]
        mode = configuration["preprocessing"]
        value = float(configuration["representative"]["cer"])
        candidate = (value, PREPROCESSING_MODES.index(mode), mode)
        if key not in selected or candidate < selected[key]:
            selected[key] = candidate
    return {key: value[2] for key, value in selected.items()}


def compare(
    source_directory: Path,
    summary_path: Path,
    output_path: Path,
    paddle_model_directory: Path,
    tesseract: Path,
    fast_tessdata: Path,
    best_tessdata: Path,
    block_size: int,
    c: int,
    threads: int,
) -> dict[str, Any]:
    source_manifest = load_json(source_directory / "manifest.json")
    summary = load_json(summary_path)
    modes = chosen_modes(summary)
    engines = {
        "paddle": PaddleEngine(paddle_model_directory, threads),
        "tesseract_fast": TesseractEngine(
            tesseract, fast_tessdata, "fast", threads
        ),
        "tesseract_best": TesseractEngine(
            tesseract, best_tessdata, "best", threads
        ),
    }
    records = []
    for sample in source_manifest["samples"]:
        condition = next(
            item for item in sample["conditions"] if item["id"] == "span_07_0p5s"
        )
        center_frame_id = condition["frame_ids"][len(condition["frame_ids"]) // 2]
        center = next(
            frame for frame in sample["frames"] if frame["id"] == center_frame_id
        )
        sources = {
            "none": source_directory / center["image_file"],
            "temporal_median": source_directory
            / "composites"
            / f"{sample['id']}_span_07_0p5s_temporal_median.png",
            "white_edge_mask": source_directory
            / "composites"
            / f"{sample['id']}_span_07_0p5s_stable_color_edge.png",
        }
        for method, path in sources.items():
            if not path.is_file():
                raise FileNotFoundError(f"PR #12 output is missing: {path}")
            source_hash = sha256_file(path)
            source = load_image(path)
            case = {
                "line_bands": [[0, source.shape[0]]],
                "ground_truth": sample["source_ko"],
            }
            for engine_key, engine in engines.items():
                raw_output = ""
                elapsed_ms = None
                error = None
                try:
                    prepared = preprocess_image(
                        source,
                        modes[engine_key],
                        adaptive_block_size=block_size,
                        adaptive_c=c,
                    )
                    raw_output, elapsed_ms = recognize_case(engine, prepared, case)
                except Exception as exception:
                    error = f"{type(exception).__name__}: {exception}"
                records.append(
                    {
                        "engine_key": engine_key,
                        "preprocessing": modes[engine_key],
                        "sample_id": sample["id"],
                        "video_id": sample["video_id"],
                        "method": method,
                        "source_image_sha256": source_hash,
                        "ground_truth": sample["source_ko"],
                        "reference_status": "ai_visual_transcription_provisional",
                        "raw_output": raw_output,
                        "inference_ms": elapsed_ms,
                        "total_ms": elapsed_ms,
                        "error": error,
                    }
                )
    scores = []
    for engine_key in engines:
        for method in ("none", "temporal_median", "white_edge_mask"):
            subset = [
                record
                for record in records
                if record["engine_key"] == engine_key and record["method"] == method
            ]
            scores.append(
                {
                    "engine_key": engine_key,
                    "preprocessing": modes[engine_key],
                    "method": method,
                    **score_records(subset),
                }
            )
    result = {
        "schema_version": 1,
        "scope": "development_exploratory",
        "independent_evaluation": False,
        "reference_status": "ai_visual_transcription_provisional",
        "source": "PR #12 fixed 7 frames over 0.5 seconds",
        "selected_preprocessing": modes,
        "adaptive": {"block_size": block_size, "c": c},
        "scores": scores,
        "records": records,
    }
    write_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paddle-model-dir", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, required=True)
    parser.add_argument("--fast-tessdata", type=Path, required=True)
    parser.add_argument("--best-tessdata", type=Path, required=True)
    parser.add_argument("--adaptive-block-size", type=int, required=True)
    parser.add_argument("--adaptive-c", type=int, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    try:
        compare(
            args.source,
            args.summary,
            args.output,
            args.paddle_model_dir,
            args.tesseract,
            args.fast_tessdata,
            args.best_tessdata,
            args.adaptive_block_size,
            args.adaptive_c,
            args.threads,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Background comparison failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
