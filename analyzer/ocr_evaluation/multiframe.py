"""Evaluate time-separated frames from stable subtitle intervals.

This development-only experiment separates frame-count and time-span effects,
preserves every OCR output, and tests two fixed image-composition candidates.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.core import edit_distance, remove_whitespace, sha256_file
else:
    from .core import edit_distance, remove_whitespace, sha256_file


COMPOSITE_METHODS = ("temporal_median", "stable_color_edge")
NESTED_FRAME_INDICES = {
    3: (0, 7, 14),
    7: (0, 2, 5, 7, 9, 12, 14),
    15: tuple(range(15)),
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_png(path: Path, image: Any) -> None:
    import cv2

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError(f"could not encode {path}")
    encoded.tofile(path)


def file_inventory(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        raise ValueError(f"model directory does not exist: {directory}")
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]


def evenly_spaced(start: float, end: float, count: int) -> list[float]:
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("frame interval must be finite, non-negative and non-empty")
    if type(count) is not int or count < 2:
        raise ValueError("frame count must be an integer of at least two")
    step = (end - start) / (count - 1)
    return [round(start + index * step, 6) for index in range(count)]


def centered_times(stable_start: float, stable_end: float, count: int, span: float) -> list[float]:
    available = stable_end - stable_start
    if not math.isfinite(span) or span <= 0 or span > available + 1e-9:
        raise ValueError("span must fit inside the stable interval")
    center = (stable_start + stable_end) / 2
    return evenly_spaced(center - span / 2, center + span / 2, count)


def nested_frame_times(start: float, end: float, count: int) -> list[float]:
    if count not in NESTED_FRAME_INDICES:
        raise ValueError("nested frame count must be 3, 7 or 15")
    grid = evenly_spaced(start, end, 15)
    return [grid[index] for index in NESTED_FRAME_INDICES[count]]


def condition_times(specification: dict[str, Any], sample: dict[str, Any]) -> dict[str, list[float]]:
    start = sample["stable_start_seconds"]
    end = sample["stable_end_seconds"]
    full_span = end - start
    conditions = {
        f"count_{count:02}_full": nested_frame_times(start, end, count)
        for count in specification["frame_counts"]
    }
    fixed_count = specification["fixed_count_for_span_comparison"]
    for span in specification["fixed_spans_seconds"]:
        if span <= full_span + 1e-9:
            label = str(span).replace(".", "p")
            conditions[f"span_{fixed_count:02}_{label}s"] = centered_times(start, end, fixed_count, span)
    return conditions


def validate_specification(specification: Any) -> None:
    if not isinstance(specification, dict) or specification.get("schema_version") != 1:
        raise ValueError("multiframe specification schema_version must be 1")
    if specification.get("purpose") != "development_only_multiframe_ocr":
        raise ValueError("multiframe experiment must be development-only")
    if specification.get("frame_counts") != [3, 7, 15]:
        raise ValueError("frame_counts must remain fixed at 3, 7 and 15")
    if specification.get("fixed_count_for_span_comparison") != 7:
        raise ValueError("fixed span comparison must use seven frames")
    sources = specification.get("sources")
    samples = specification.get("samples")
    if not isinstance(sources, dict) or not sources or not isinstance(samples, list) or not samples:
        raise ValueError("sources and samples are required")
    for video_id, source in sources.items():
        if source.get("split") not in {"development_original", "development_additional"}:
            raise ValueError(f"source {video_id} is not a development split")
        digest = source.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"source {video_id} needs a SHA-256 digest")
    identifiers: set[str] = set()
    for sample in samples:
        identifier = sample.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("sample ids must be unique non-empty strings")
        identifiers.add(identifier)
        if sample.get("video_id") not in sources:
            raise ValueError(f"sample {identifier} has an unknown video_id")
        source = sources[sample["video_id"]]
        box = sample.get("crop_xywh")
        if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
            raise ValueError(f"sample {identifier} has an invalid crop")
        x, y, width, height = box
        if min(x, y) < 0 or min(width, height) <= 0 or x + width > source["width"] or y + height > source["height"]:
            raise ValueError(f"sample {identifier} crop is outside the video")
        if not isinstance(sample.get("source_ko"), str) or not remove_whitespace(sample["source_ko"]):
            raise ValueError(f"sample {identifier} needs source_ko")
        condition_times(specification, sample)


def parse_video_mapping(values: list[str]) -> dict[str, Path]:
    mappings: dict[str, Path] = {}
    for value in values:
        video_id, separator, path = value.partition("=")
        if not separator or not video_id or not path or video_id in mappings:
            raise ValueError("--video must be a unique VIDEO_ID=PATH mapping")
        mappings[video_id] = Path(path)
    return mappings


def decode_frame(video: Path, timestamp: float) -> Any:
    import cv2
    import numpy as np

    encoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(video),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"ffmpeg returned an undecodable frame at {timestamp}")
    return frame


def extract(specification_path: Path, videos: dict[str, Path], destination: Path) -> dict[str, Any]:
    specification = read_json(specification_path)
    validate_specification(specification)
    if set(videos) != set(specification["sources"]):
        raise ValueError("video mappings must exactly match the development sources")
    destination.mkdir(parents=True, exist_ok=False)
    source_records = {}
    for video_id, expected in specification["sources"].items():
        path = videos[video_id]
        if not path.is_file() or sha256_file(path) != expected["sha256"]:
            raise ValueError(f"video {video_id} is missing or has the wrong digest")
        source_records[video_id] = {"sha256": expected["sha256"], "size_bytes": path.stat().st_size}

    extracted_samples = []
    for sample in specification["samples"]:
        conditions = condition_times(specification, sample)
        timestamps = sorted({timestamp for values in conditions.values() for timestamp in values})
        frame_by_timestamp = {}
        for index, timestamp in enumerate(timestamps, 1):
            frame = decode_frame(videos[sample["video_id"]], timestamp)
            source = specification["sources"][sample["video_id"]]
            if frame.shape[:2] != (source["height"], source["width"]):
                raise ValueError(f"video dimensions changed for {sample['video_id']}")
            x, y, width, height = sample["crop_xywh"]
            image_file = f"{sample['id']}_f{index:03}.png"
            image_path = destination / image_file
            write_png(image_path, frame[y:y + height, x:x + width])
            frame_by_timestamp[timestamp] = {
                "id": f"{sample['id']}_f{index:03}", "timestamp_seconds": timestamp,
                "image_file": image_file, "image_sha256": sha256_file(image_path),
            }
        extracted_samples.append({
            **sample,
            "frames": [frame_by_timestamp[timestamp] for timestamp in timestamps],
            "conditions": [{"id": identifier, "frame_ids": [frame_by_timestamp[t]["id"] for t in times],
                            "timestamps_seconds": times} for identifier, times in conditions.items()],
        })
    manifest = {
        "schema_version": 1,
        "specification_sha256": sha256_file(specification_path),
        "specification": specification,
        "sources": source_records,
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        "samples": extracted_samples,
    }
    save_json(destination / "manifest.json", manifest)
    return manifest


def normalized_distance(left: str, right: str) -> float:
    left_clean = remove_whitespace(left)
    right_clean = remove_whitespace(right)
    return edit_distance(left_clean, right_clean) / max(len(left_clean), len(right_clean), 1)


def choose_medoid(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("cannot choose a medoid without OCR records")
    return min(records, key=lambda candidate: (
        sum(normalized_distance(candidate["text"], other["text"]) for other in records),
        -float(candidate.get("confidence") or 0), candidate["frame_id"],
    ))


def choose_highest_confidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("cannot choose a result without OCR records")
    return max(records, key=lambda record: (float(record.get("confidence") or 0), record["frame_id"]))


def make_composite(images: list[Any], method: str) -> Any:
    import cv2
    import numpy as np

    if method not in COMPOSITE_METHODS or not images:
        raise ValueError("unknown composite method or empty image list")
    if len({image.shape for image in images}) != 1:
        raise ValueError("composite images must have matching shapes")
    stack = np.stack(images).astype(np.uint8)
    median = np.median(stack, axis=0).astype(np.uint8)
    if method == "temporal_median":
        return median

    color_masks = []
    edge_masks = []
    for image in images:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        white = (value >= 160) & (saturation <= 110)
        yellow = (hue >= 15) & (hue <= 40) & (saturation >= 70) & (value >= 130)
        color_masks.append(white | yellow)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edge_masks.append(cv2.Canny(gray, 60, 150) > 0)
    stable_color = np.mean(np.stack(color_masks), axis=0) >= 0.6
    stable_edge = np.mean(np.stack(edge_masks), axis=0) >= 0.6
    kernel = np.ones((3, 3), np.uint8)
    text_neighborhood = cv2.dilate(stable_color.astype(np.uint8), kernel, iterations=2) > 0
    retained = text_neighborhood | stable_edge
    output = np.zeros_like(median)
    output[retained] = median[retained]
    return output


def text_metrics(reference: str, prediction: str) -> dict[str, Any]:
    reference_plain = remove_whitespace(reference)
    prediction_plain = remove_whitespace(prediction)
    return {
        "edits": edit_distance(reference, prediction), "characters": len(reference),
        "cer": edit_distance(reference, prediction) / len(reference),
        "whitespace_free_edits": edit_distance(reference_plain, prediction_plain),
        "whitespace_free_characters": len(reference_plain),
        "whitespace_free_cer": edit_distance(reference_plain, prediction_plain) / len(reference_plain),
        "exact": reference == prediction,
        "whitespace_free_exact": reference_plain == prediction_plain,
    }


def aggregate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        for method, result in row["outputs"].items():
            groups.setdefault((row["condition_id"], method), []).append(result)
    summary = []
    for (condition_id, method), results in sorted(groups.items()):
        characters = sum(item["metrics"]["characters"] for item in results)
        plain_characters = sum(item["metrics"]["whitespace_free_characters"] for item in results)
        summary.append({
            "condition_id": condition_id, "method": method, "sample_count": len(results),
            "cer": sum(item["metrics"]["edits"] for item in results) / characters,
            "whitespace_free_cer": sum(item["metrics"]["whitespace_free_edits"] for item in results) / plain_characters,
            "exact": sum(item["metrics"]["exact"] for item in results),
            "whitespace_free_exact": sum(item["metrics"]["whitespace_free_exact"] for item in results),
            "ocr_ms_median": statistics.median(item["ocr_ms"] for item in results),
            "total_pipeline_ms_median": statistics.median(item["total_pipeline_ms"] for item in results),
        })
    return {"rows": summary}


def recognise(directory: Path, model_directory: Path) -> dict[str, Any]:
    model_cache_root = model_directory.resolve()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(model_cache_root)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["MKL_NUM_THREADS"] = "4"

    manifest = read_json(directory / "manifest.json")
    validate_specification(manifest.get("specification"))
    if (directory / "results.json").exists():
        raise ValueError("results.json already exists; use a separate experiment directory")

    # PaddleX resolves its cache location while importing, so configure the
    # environment before importing PaddleOCR. This also makes the recorded
    # model location match the files actually used by the experiment.
    from paddleocr import TextRecognition
    from analyzer.ocr_evaluation.preprocessing import load_image, preprocess
    from analyzer.ocr_evaluation.run_paddle import result_text_and_score

    started = time.perf_counter()
    model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", device="cpu", engine="paddle_static",
                            enable_hpi=False, enable_mkldnn=True, cpu_threads=4)
    initialization_ms = (time.perf_counter() - started) * 1000

    first = manifest["samples"][0]["frames"][0]
    warmup = preprocess(load_image(directory / first["image_file"]), "contrast")
    for _ in range(3):
        list(model.predict(input=warmup, batch_size=1))

    def ocr_preprocessed(image: Any) -> tuple[str, float | None, float]:
        started_at = time.perf_counter()
        predictions = list(model.predict(input=image, batch_size=1))
        elapsed = (time.perf_counter() - started_at) * 1000
        if len(predictions) != 1:
            raise ValueError("expected one PaddleOCR result")
        text, confidence = result_text_and_score(predictions[0])
        return text, confidence, elapsed

    composites = directory / "composites"
    preprocessed_images = directory / "preprocessed"
    composites.mkdir()
    preprocessed_images.mkdir()
    rows = []
    for sample in manifest["samples"]:
        images = {}
        individual = {}
        for frame in sample["frames"]:
            path = directory / frame["image_file"]
            if sha256_file(path) != frame["image_sha256"]:
                raise ValueError(f"extracted frame changed: {frame['id']}")
            image = load_image(path)
            images[frame["id"]] = image
            prepared = preprocess(image, "contrast")
            prepared_file = f"{frame['id']}_contrast.png"
            prepared_path = preprocessed_images / prepared_file
            write_png(prepared_path, prepared)
            text, confidence, elapsed = ocr_preprocessed(prepared)
            individual[frame["id"]] = {"frame_id": frame["id"], "text": text,
                                       "confidence": confidence, "ocr_ms": elapsed,
                                       "preprocessed_image_file": f"preprocessed/{prepared_file}",
                                       "preprocessed_image_sha256": sha256_file(prepared_path)}
        for condition in sample["conditions"]:
            frame_records = [individual[identifier] for identifier in condition["frame_ids"]]
            input_frame_ocr_ms_total = sum(item["ocr_ms"] for item in frame_records)
            outputs = {}
            for method, selector in (("highest_confidence", choose_highest_confidence),
                                     ("medoid", choose_medoid)):
                selection_started = time.perf_counter()
                selected = selector(frame_records)
                selection_ms = (time.perf_counter() - selection_started) * 1000
                outputs[method] = {
                    **selected,
                    "selection_ms": selection_ms,
                    "input_frame_ocr_ms_total": input_frame_ocr_ms_total,
                    "total_pipeline_ms": input_frame_ocr_ms_total + selection_ms,
                    "metrics": text_metrics(sample["source_ko"], selected["text"]),
                }
            for method in COMPOSITE_METHODS:
                started_at = time.perf_counter()
                composite = make_composite([images[identifier] for identifier in condition["frame_ids"]], method)
                composite_ms = (time.perf_counter() - started_at) * 1000
                file_name = f"{sample['id']}_{condition['id']}_{method}.png"
                path = composites / file_name
                write_png(path, composite)
                text, confidence, elapsed = ocr_preprocessed(preprocess(composite, "contrast"))
                outputs[method] = {
                    "text": text, "confidence": confidence, "ocr_ms": elapsed,
                    "composition_ms": composite_ms, "image_file": f"composites/{file_name}",
                    "total_pipeline_ms": composite_ms + elapsed,
                    "image_sha256": sha256_file(path), "metrics": text_metrics(sample["source_ko"], text),
                }
            rows.append({
                "sample_id": sample["id"], "video_id": sample["video_id"],
                "source_ko": sample["source_ko"], "reference_status": manifest["specification"]["reference_status"],
                "condition_id": condition["id"], "frame_ids": condition["frame_ids"],
                "timestamps_seconds": condition["timestamps_seconds"], "individual_outputs": frame_records,
                "agreement": len({remove_whitespace(item["text"]) for item in frame_records}) == 1,
                "outputs": outputs,
            })
    result = {
        "schema_version": 1, "manifest_sha256": sha256_file(directory / "manifest.json"),
        "model": "korean_PP-OCRv5_mobile_rec", "preprocessing": "contrast",
        "model_cache_root": str(model_cache_root),
        "resolved_model_directory": str(model_cache_root / "official_models" / "korean_PP-OCRv5_mobile_rec"),
        "model_files": file_inventory(model_cache_root / "official_models" / "korean_PP-OCRv5_mobile_rec"),
        "composite_methods": list(COMPOSITE_METHODS), "cpu_threads": 4,
        "paddleocr": importlib.metadata.version("paddleocr"),
        "paddlepaddle": importlib.metadata.version("paddlepaddle"),
        "initialization_ms": initialization_ms, "rows": rows,
    }
    save_json(directory / "results.json", result)
    save_json(directory / "summary.json", aggregate_summary(rows))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extraction = subparsers.add_parser("extract")
    extraction.add_argument("--specification", type=Path, required=True)
    extraction.add_argument("--video", action="append", default=[], metavar="VIDEO_ID=PATH")
    extraction.add_argument("--output", type=Path, required=True)
    recognition = subparsers.add_parser("recognise")
    recognition.add_argument("--input", type=Path, required=True)
    recognition.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "extract":
        extract(args.specification, parse_video_mapping(args.video), args.output)
    else:
        recognise(args.input, args.model_dir)


if __name__ == "__main__":
    main()
