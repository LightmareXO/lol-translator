"""Issue #19 OCR comparison pipeline.

The commands in this module only create evaluation artefacts under a caller supplied
directory.  They do not change the application's OCR path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

from .core import (
    edit_distance,
    normalize_text,
    normalized_region_to_pixels,
    percentile,
    remove_whitespace,
)


PREPROCESSING_MODES = (
    "original",
    "contrast",
    "grayscale",
    "otsu",
    "adaptive",
    "contrast_otsu",
    "contrast_adaptive",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_image(path: Path) -> Any:
    import cv2
    import numpy as np

    encoded = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"画像を読み込めません: {path}")
    return image


def save_image(path: Path, image: Any) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError(f"PNGへ変換できません: {path}")
    encoded.tofile(path)


def contrast_1_5(image: Any) -> Any:
    import numpy as np

    return np.clip((image.astype(np.float32) - 127.5) * 1.5 + 127.5, 0, 255).astype(
        np.uint8
    )


def preprocess_image(
    image: Any,
    mode: str,
    *,
    adaptive_block_size: int,
    adaptive_c: int,
) -> Any:
    """Apply one of the seven shared inputs without resizing or morphology."""
    import cv2

    if mode not in PREPROCESSING_MODES:
        raise ValueError(f"未対応の前処理です: {mode}")
    if adaptive_block_size < 3 or adaptive_block_size % 2 == 0:
        raise ValueError("adaptive block_size must be an odd integer >= 3")
    if mode == "original":
        return image.copy()
    contrasted = contrast_1_5(image) if mode.startswith("contrast_") else image
    if mode == "contrast":
        return contrast_1_5(image)
    gray = cv2.cvtColor(contrasted, cv2.COLOR_BGR2GRAY)
    if mode == "grayscale":
        output = gray
    elif mode in {"otsu", "contrast_otsu"}:
        _, output = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
        )
    elif mode in {"adaptive", "contrast_adaptive"}:
        output = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            adaptive_block_size,
            adaptive_c,
        )
    else:  # pragma: no cover - guarded by PREPROCESSING_MODES
        raise AssertionError(mode)
    return cv2.cvtColor(output, cv2.COLOR_GRAY2BGR)


def strip_cli_trailing_newlines(text: str) -> str:
    """Remove only CR/LF emitted after the final Tesseract text line."""
    return text.rstrip("\r\n")


def split_lines(image: Any, line_bands: list[list[int]]) -> list[Any]:
    height = image.shape[0]
    result = []
    previous_end = 0
    for index, band in enumerate(line_bands):
        if len(band) != 2:
            raise ValueError(f"line_bands[{index}] must have start and end")
        start, end = band
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("line band coordinates must be integers")
        if start < previous_end or end <= start or end > height:
            raise ValueError("line bands must be ordered, non-overlapping and in bounds")
        result.append(image[start:end, :])
        previous_end = end
    if not result:
        raise ValueError("at least one line band is required")
    return result


def join_line_outputs(outputs: list[str]) -> str:
    return "\n".join(strip_cli_trailing_newlines(output) for output in outputs)


def score_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    reference_characters = 0
    whitespace_free_reference_characters = 0
    errors = 0
    whitespace_free_errors = 0
    exact_matches = 0
    whitespace_free_exact_matches = 0
    failures = 0
    empty_outputs = 0
    inference_times: list[float] = []
    total_times: list[float] = []
    for record in records:
        reference = normalize_text(record["ground_truth"])
        prediction = normalize_text(record.get("raw_output") or "")
        reference_characters += len(reference)
        errors += edit_distance(reference, prediction)
        reference_compact = remove_whitespace(reference)
        prediction_compact = remove_whitespace(prediction)
        whitespace_free_reference_characters += len(reference_compact)
        whitespace_free_errors += edit_distance(reference_compact, prediction_compact)
        exact_matches += reference == prediction
        whitespace_free_exact_matches += reference_compact == prediction_compact
        failures += bool(record.get("error"))
        empty_outputs += not prediction
        if record.get("inference_ms") is not None:
            inference_times.append(float(record["inference_ms"]))
        if record.get("total_ms") is not None:
            total_times.append(float(record["total_ms"]))
    return {
        "image_count": len(records),
        "reference_characters": reference_characters,
        "edit_distance": errors,
        "cer": errors / reference_characters if reference_characters else None,
        "whitespace_free_reference_characters": whitespace_free_reference_characters,
        "whitespace_free_edit_distance": whitespace_free_errors,
        "whitespace_free_cer": (
            whitespace_free_errors / whitespace_free_reference_characters
            if whitespace_free_reference_characters
            else None
        ),
        "exact_matches": exact_matches,
        "whitespace_free_exact_matches": whitespace_free_exact_matches,
        "failure_count": failures,
        "empty_output_count": empty_outputs,
        "inference_ms_median": statistics.median(inference_times)
        if inference_times
        else None,
        "inference_ms_p95": percentile(inference_times, 0.95)
        if inference_times
        else None,
        "total_ms_median": statistics.median(total_times) if total_times else None,
        "total_ms_p95": percentile(total_times, 0.95) if total_times else None,
    }


def stability_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record["group_id"], []).append(record)
    rows = []
    for group_id, group_records in sorted(groups.items()):
        ordered = sorted(group_records, key=lambda item: item["timestamp_seconds"])
        if len(ordered) < 2:
            continue
        changed = 0
        distances = []
        for left, right in zip(ordered, ordered[1:]):
            left_text = normalize_text(left.get("raw_output") or "")
            right_text = normalize_text(right.get("raw_output") or "")
            changed += left_text != right_text
            distances.append(
                edit_distance(left_text, right_text)
                / max(len(left_text), len(right_text), 1)
            )
        rows.append(
            {
                "group_id": group_id,
                "adjacent_pairs": len(distances),
                "changed_pairs": changed,
                "change_rate": changed / len(distances),
                "mean_normalized_edit_distance": statistics.fmean(distances),
            }
        )
    return {
        "group_count": len(rows),
        "adjacent_pair_count": sum(row["adjacent_pairs"] for row in rows),
        "changed_pair_count": sum(row["changed_pairs"] for row in rows),
        "change_rate": (
            sum(row["changed_pairs"] for row in rows)
            / sum(row["adjacent_pairs"] for row in rows)
            if rows
            else None
        ),
        "group_mean_normalized_edit_distance": (
            statistics.fmean(row["mean_normalized_edit_distance"] for row in rows)
            if rows
            else None
        ),
        "groups": rows,
    }


def parse_video_arguments(values: list[str]) -> dict[str, Path]:
    videos: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--video must use VIDEO_ID=PATH")
        video_id, path_text = value.split("=", 1)
        if not video_id or video_id in videos:
            raise ValueError(f"invalid or duplicate video id: {video_id}")
        videos[video_id] = Path(path_text)
    return videos


def prepare_dataset(
    spec_path: Path,
    initial_images: Path,
    videos: dict[str, Path],
    output_directory: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    spec_text = spec_path.read_text(encoding="utf-8")
    spec = json.loads(spec_text)
    initial_manifest_path = spec_path.parent / spec["initial_dataset"]["manifest"]
    initial = load_json(initial_manifest_path)
    images_directory = output_directory / "images"
    images_directory.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    initial_crop = list(
        normalized_region_to_pixels(
            initial["source"]["subtitle_region"],
            initial["source"]["width"],
            initial["source"]["height"],
        )
    )
    for item in initial["images"]:
        source = initial_images / item["image_file"]
        if not source.is_file():
            raise FileNotFoundError(f"初期画像がありません: {source}")
        destination = images_directory / item["image_file"]
        shutil.copyfile(source, destination)
        height = load_image(destination).shape[0]
        cases.append(
            {
                "id": item["id"],
                "group_id": item["group_id"],
                "video_id": initial["source"]["video_id"],
                "timestamp_seconds": item["timestamp_seconds"],
                "crop_xywh": initial_crop,
                "image_file": item["image_file"],
                "image_sha256": sha256_file(destination),
                "ground_truth": item["ground_truth"],
                "reference_status": spec["initial_dataset"]["reference_status"],
                "reference_verifier": "Codex AI visual transcription; no human confirmation record",
                "split": spec["initial_dataset"]["split"],
                "prior_development_use": True,
                "line_bands": [[0, height]],
                "line_count": 1,
                "tags": item["tags"],
                "representative": item["id"].endswith("_f02"),
            }
        )
    for item in spec["additional_cases"]:
        source_info = spec["additional_sources"][item["video_id"]]
        video = videos.get(item["video_id"])
        if video is None or not video.is_file():
            raise FileNotFoundError(f"追加動画がありません: {item['video_id']}")
        if sha256_file(video) != source_info["sha256"]:
            raise ValueError(f"動画SHA-256が一致しません: {item['video_id']}")
        destination = images_directory / f"{item['id']}.png"
        x, y, width, height = item["crop_xywh"]
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(item["timestamp_seconds"]),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"crop={width}:{height}:{x}:{y}",
            "-y",
            str(destination),
        ]
        subprocess.run(command, check=True)
        actual = load_image(destination)
        if actual.shape[0] != height or actual.shape[1] != width:
            raise ValueError(f"抽出画像の寸法が一致しません: {item['id']}")
        cases.append(
            {
                "id": item["id"],
                "group_id": item["id"],
                "video_id": item["video_id"],
                "timestamp_seconds": item["timestamp_seconds"],
                "crop_xywh": item["crop_xywh"],
                "image_file": destination.name,
                "image_sha256": sha256_file(destination),
                "ground_truth": item["ground_truth"],
                "reference_status": source_info["reference_status"],
                "reference_verifier": "Codex AI visual transcription; no human confirmation record",
                "split": source_info["split"],
                "prior_development_use": True,
                "line_bands": item["line_bands"],
                "line_count": len(item["line_bands"]),
                "tags": item["tags"],
                "representative": True,
            }
        )
    prepared = {
        "schema_version": 1,
        "specification_sha256": sha256_text(spec_text),
        "independent_evaluation": spec["independent_evaluation"],
        "reference_policy": spec["reference_policy"],
        "cases": cases,
    }
    write_json(output_directory / "prepared.json", prepared)
    return prepared


def materialize_preprocessing(
    prepared_path: Path,
    output_directory: Path,
    block_size: int,
    c: int,
) -> dict[str, Any]:
    prepared = load_json(prepared_path)
    source_directory = prepared_path.parent / "images"
    records: list[dict[str, Any]] = []
    for case in prepared["cases"]:
        source = source_directory / case["image_file"]
        if sha256_file(source) != case["image_sha256"]:
            raise ValueError(f"入力画像ハッシュが一致しません: {case['id']}")
        image = load_image(source)
        for mode in PREPROCESSING_MODES:
            output = output_directory / mode / case["image_file"]
            save_image(
                output,
                preprocess_image(
                    image,
                    mode,
                    adaptive_block_size=block_size,
                    adaptive_c=c,
                ),
            )
            records.append(
                {
                    "case_id": case["id"],
                    "mode": mode,
                    "file": str(Path(mode) / case["image_file"]),
                    "sha256": sha256_file(output),
                }
            )
    manifest = {
        "schema_version": 1,
        "prepared_sha256": sha256_file(prepared_path),
        "adaptive": {
            "method": "OpenCV Gaussian adaptive threshold",
            "block_size": block_size,
            "c": c,
            "polarity": "THRESH_BINARY_INV: bright source text becomes dark on a light background",
        },
        "contrast": "1.5x linear contrast around midpoint 127.5; not histogram equalization",
        "resize": "none",
        "smoothing": "none",
        "morphology": "none",
        "records": records,
    }
    write_json(output_directory / "inputs.json", manifest)
    return manifest


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class TesseractEngine:
    def __init__(
        self,
        executable: Path,
        tessdata: Path,
        model_name: str,
        threads: int,
    ) -> None:
        if not executable.is_file():
            raise FileNotFoundError(f"Tesseractがありません: {executable}")
        for language in ("kor", "eng"):
            if not (tessdata / f"{language}.traineddata").is_file():
                raise FileNotFoundError(
                    f"言語モデルがありません: {tessdata / f'{language}.traineddata'}"
                )
        self.executable = executable
        self.tessdata = tessdata
        self.model_name = model_name
        self.threads = threads
        self.peak_rss_bytes = 0

    def recognize(self, image: Any, psm: int = 7) -> tuple[str, float]:
        import psutil

        with tempfile.TemporaryDirectory(prefix="loltr-tesseract-") as temporary:
            image_path = Path(temporary) / "input.png"
            save_image(image_path, image)
            command = [
                str(self.executable),
                str(image_path),
                "stdout",
                "-l",
                "kor+eng",
                "--tessdata-dir",
                str(self.tessdata),
                "--oem",
                "1",
                "--psm",
                str(psm),
            ]
            environment = os.environ.copy()
            environment["OMP_THREAD_LIMIT"] = str(self.threads)
            started = time.perf_counter()
            process = psutil.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
            while process.poll() is None:
                try:
                    self.peak_rss_bytes = max(
                        self.peak_rss_bytes, process.memory_info().rss
                    )
                except psutil.Error:
                    pass
                time.sleep(0.005)
            stdout, stderr = process.communicate()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if process.returncode != 0:
                raise RuntimeError(
                    f"Tesseract終了コード{process.returncode}: {stderr.strip()}"
                )
            return strip_cli_trailing_newlines(stdout), elapsed_ms

    def metadata(self) -> dict[str, Any]:
        version = subprocess.run(
            [str(self.executable), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout.splitlines()[0]
        root = self.executable.parent
        return {
            "engine": "tesseract",
            "engine_version": version,
            "model": self.model_name,
            "model_files": {
                language: {
                    "sha256": sha256_file(self.tessdata / f"{language}.traineddata"),
                    "bytes": (self.tessdata / f"{language}.traineddata").stat().st_size,
                }
                for language in ("kor", "eng")
            },
            "executable_sha256": sha256_file(self.executable),
            "distribution_directory_bytes": _directory_size(root),
            "dependency_dlls": sorted(item.name for item in root.glob("*.dll")),
            "language_order": "kor+eng",
            "oem": 1,
            "one_line_psm": 7,
            "two_line_manual_split_psm": 7,
            "two_line_whole_roi_diagnostic_psm": 6,
            "threads": self.threads,
            "timing_scope": "one subprocess per line; includes process startup and model load",
        }


class PaddleEngine:
    def __init__(self, model_directory: Path, threads: int) -> None:
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(model_directory.resolve())
        os.environ["PADDLE_PDX_MODEL_SOURCE"] = "bos"
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["MKL_NUM_THREADS"] = str(threads)
        from paddleocr import TextRecognition

        started = time.perf_counter()
        self.model = TextRecognition(
            model_name="korean_PP-OCRv5_mobile_rec",
            device="cpu",
            engine="paddle_static",
            enable_hpi=False,
            enable_mkldnn=True,
            cpu_threads=threads,
        )
        self.initialization_ms = (time.perf_counter() - started) * 1000
        self.model_directory = model_directory
        self.threads = threads

    def recognize(self, image: Any, psm: int = 7) -> tuple[str, float]:
        del psm
        started = time.perf_counter()
        results = list(self.model.predict(input=image, batch_size=1))
        elapsed_ms = (time.perf_counter() - started) * 1000
        if len(results) != 1:
            raise ValueError(f"PaddleOCR result count: {len(results)}")
        payload = getattr(results[0], "json", results[0])
        if callable(payload):
            payload = payload()
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict) and "res" in payload:
            payload = payload["res"]
        if not isinstance(payload, dict) or "rec_text" not in payload:
            raise ValueError("PaddleOCR response has no rec_text")
        return str(payload["rec_text"]), elapsed_ms

    def metadata(self) -> dict[str, Any]:
        import importlib.metadata
        import psutil

        return {
            "engine": "paddleocr",
            "engine_version": importlib.metadata.version("paddleocr"),
            "paddlepaddle_version": importlib.metadata.version("paddlepaddle"),
            "model": "korean_PP-OCRv5_mobile_rec",
            "model_directory_bytes": _directory_size(self.model_directory),
            "threads": self.threads,
            "initialization_ms": self.initialization_ms,
            "resident_rss_bytes_after_initialization": psutil.Process().memory_info().rss,
            "timing_scope": "resident recognizer call; excludes process startup and model initialization",
        }


def recognize_case(engine: Any, image: Any, case: dict[str, Any]) -> tuple[str, float]:
    outputs: list[str] = []
    elapsed_ms = 0.0
    for line in split_lines(image, case["line_bands"]):
        output, line_ms = engine.recognize(line, psm=7)
        outputs.append(output)
        elapsed_ms += line_ms
    return join_line_outputs(outputs), elapsed_ms


def run_engine(
    engine: Any,
    engine_key: str,
    prepared_path: Path,
    inputs_path: Path,
    output_path: Path,
    warmup_count: int,
) -> dict[str, Any]:
    prepared = load_json(prepared_path)
    inputs = load_json(inputs_path)
    if inputs["prepared_sha256"] != sha256_file(prepared_path):
        raise ValueError("前処理画像とprepared.jsonが一致しません")
    inputs_directory = inputs_path.parent
    first_case = prepared["cases"][0]
    first_image = load_image(
        inputs_directory / "original" / first_case["image_file"]
    )
    cold_output, cold_ms = recognize_case(engine, first_image, first_case)
    warmup_times = []
    for _ in range(warmup_count):
        _, elapsed_ms = recognize_case(engine, first_image, first_case)
        warmup_times.append(elapsed_ms)
    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for mode in PREPROCESSING_MODES:
        for case in prepared["cases"]:
            raw_output = ""
            error = None
            inference_ms = None
            started = time.perf_counter()
            try:
                image_path = inputs_directory / mode / case["image_file"]
                expected = next(
                    item["sha256"]
                    for item in inputs["records"]
                    if item["case_id"] == case["id"] and item["mode"] == mode
                )
                if sha256_file(image_path) != expected:
                    raise ValueError("前処理画像ハッシュが一致しません")
                image = load_image(image_path)
                raw_output, inference_ms = recognize_case(engine, image, case)
                if engine_key.startswith("tesseract") and case["line_count"] == 2:
                    diagnostic_output, diagnostic_ms = engine.recognize(image, psm=6)
                    diagnostics.append(
                        {
                            "case_id": case["id"],
                            "preprocessing": mode,
                            "psm": 6,
                            "raw_output": diagnostic_output,
                            "inference_ms": diagnostic_ms,
                        }
                    )
            except Exception as exception:  # Persist failures as empty predictions.
                error = f"{type(exception).__name__}: {exception}"
                raw_output = ""
            total_ms = (time.perf_counter() - started) * 1000
            records.append(
                {
                    **case,
                    "preprocessing": mode,
                    "raw_output": raw_output,
                    "normalized_output": normalize_text(raw_output),
                    "inference_ms": inference_ms,
                    "total_ms": total_ms,
                    "error": error,
                }
            )
    metadata = engine.metadata()
    if hasattr(engine, "peak_rss_bytes"):
        metadata["peak_subprocess_rss_bytes"] = engine.peak_rss_bytes
    result = {
        "schema_version": 1,
        "engine_key": engine_key,
        "prepared_sha256": sha256_file(prepared_path),
        "inputs_sha256": sha256_file(inputs_path),
        "metadata": metadata,
        "cold_probe": {"raw_output": cold_output, "elapsed_ms": cold_ms},
        "warmup_count": warmup_count,
        "warmup_times_ms": warmup_times,
        "records": records,
        "two_line_whole_roi_diagnostics": diagnostics,
    }
    write_json(output_path, result)
    return result


def aggregate_results(result_paths: list[Path], output_path: Path) -> dict[str, Any]:
    results = [load_json(path) for path in result_paths]
    if not results:
        raise ValueError("結果ファイルが必要です")
    prepared_sha256 = results[0]["prepared_sha256"]
    inputs_sha256 = results[0]["inputs_sha256"]
    if any(
        result["prepared_sha256"] != prepared_sha256
        or result["inputs_sha256"] != inputs_sha256
        for result in results
    ):
        raise ValueError("異なる入力の結果を集計できません")
    configurations = []
    detail_records = []
    for result in results:
        for mode in PREPROCESSING_MODES:
            records = [
                record
                for record in result["records"]
                if record["preprocessing"] == mode
            ]
            representative = [record for record in records if record["representative"]]
            by_video = {
                video_id: score_records(
                    [record for record in representative if record["video_id"] == video_id]
                )
                for video_id in sorted({record["video_id"] for record in representative})
            }
            by_line_count = {
                str(line_count): score_records(
                    [record for record in representative if record["line_count"] == line_count]
                )
                for line_count in sorted({record["line_count"] for record in representative})
            }
            configurations.append(
                {
                    "engine_key": result["engine_key"],
                    "preprocessing": mode,
                    "representative": score_records(representative),
                    "all_adjacent_frames": score_records(records),
                    "adjacent_frame_stability": stability_summary(records),
                    "by_video": by_video,
                    "by_line_count": by_line_count,
                }
            )
        detail_records.extend(
            {"engine_key": result["engine_key"], **record}
            for record in result["records"]
        )
    summary = {
        "schema_version": 1,
        "prepared_sha256": prepared_sha256,
        "inputs_sha256": inputs_sha256,
        "reference_status": "ai_visual_transcription_provisional",
        "human_confirmed_subtitles": 0,
        "independent_evaluation": False,
        "metric": "Unicode NFC code-point corpus CER; punctuation, case, numbers and internal line breaks retained",
        "cli_newline_policy": "strip trailing CR and LF only; preserve internal line breaks",
        "failure_policy": "score failures as empty predictions and count separately",
        "configurations": configurations,
        "engine_metadata": {
            result["engine_key"]: result["metadata"] for result in results
        },
        "cold_and_warmup": {
            result["engine_key"]: {
                "cold_probe": result["cold_probe"],
                "warmup_count": result["warmup_count"],
                "warmup_times_ms": result["warmup_times_ms"],
            }
            for result in results
        },
        "two_line_whole_roi_diagnostics": {
            result["engine_key"]: result["two_line_whole_roi_diagnostics"]
            for result in results
            if result["two_line_whole_roi_diagnostics"]
        },
    }
    write_json(output_path, summary)
    write_json(output_path.with_name("tesseract-details.json"), detail_records)
    return summary


def make_engine(args: argparse.Namespace) -> tuple[Any, str]:
    if args.engine == "paddle":
        return PaddleEngine(args.model_dir, args.threads), "paddle"
    return (
        TesseractEngine(
            args.tesseract,
            args.tessdata,
            args.engine,
            args.threads,
        ),
        f"tesseract_{args.engine}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--spec", type=Path, required=True)
    prepare.add_argument("--initial-images", type=Path, required=True)
    prepare.add_argument("--video", action="append", default=[])
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--ffmpeg", default="ffmpeg")

    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--prepared", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.add_argument("--adaptive-block-size", type=int, required=True)
    materialize.add_argument("--adaptive-c", type=int, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--engine", choices=("paddle", "fast", "best"), required=True)
    run.add_argument("--prepared", type=Path, required=True)
    run.add_argument("--inputs", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--model-dir", type=Path)
    run.add_argument("--tesseract", type=Path)
    run.add_argument("--tessdata", type=Path)
    run.add_argument("--threads", type=int, default=4)
    run.add_argument("--warmup", type=int, default=3)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--results", type=Path, nargs="+", required=True)
    aggregate.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_dataset(
                args.spec,
                args.initial_images,
                parse_video_arguments(args.video),
                args.output,
                args.ffmpeg,
            )
        elif args.command == "materialize":
            materialize_preprocessing(
                args.prepared,
                args.output,
                args.adaptive_block_size,
                args.adaptive_c,
            )
        elif args.command == "run":
            if args.threads <= 0 or args.warmup <= 0:
                raise ValueError("threads and warmup must be positive")
            if args.engine == "paddle" and args.model_dir is None:
                raise ValueError("PaddleOCRには--model-dirが必要です")
            if args.engine != "paddle" and (
                args.tesseract is None or args.tessdata is None
            ):
                raise ValueError("Tesseractには--tesseractと--tessdataが必要です")
            engine, key = make_engine(args)
            run_engine(
                engine,
                key,
                args.prepared,
                args.inputs,
                args.output,
                args.warmup,
            )
        else:
            aggregate_results(args.results, args.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Issue #19 evaluation failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
