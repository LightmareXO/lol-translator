"""Runtime helpers shared by isolated OCR engine processes."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
from typing import Any, Callable

import psutil

from .core import load_manifest, normalize_text
from .preprocessing import PREPROCESSING_DESCRIPTION, load_image, preprocess


class MemorySampler:
    """Sample parent and live child RSS for one isolated engine process."""

    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self.peak_parent_rss_bytes = 0
        self.peak_tree_rss_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self._record()

    def _record(self) -> None:
        process = psutil.Process()
        try:
            parent_rss = process.memory_info().rss
            child_rss = sum(
                child.memory_info().rss for child in process.children(recursive=True)
            )
        except (psutil.Error, OSError):
            return
        self.peak_parent_rss_bytes = max(self.peak_parent_rss_bytes, parent_rss)
        self.peak_tree_rss_bytes = max(
            self.peak_tree_rss_bytes, parent_rss + child_rss
        )

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._record()


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def installed_packages() -> list[str]:
    packages = {
        f"{distribution.metadata['Name']}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    return sorted(packages, key=str.casefold)


def run_evaluation(
    *,
    engine: str,
    model_id: str,
    engine_version: str,
    manifest_path: Path,
    images_directory: Path,
    output_path: Path,
    model_directory: Path,
    preprocessing_mode: str,
    threads: int,
    warmup_count: int,
    initialization_ms: float,
    recognize: Callable[[Any], tuple[str, float | None]],
    sampler: MemorySampler,
    process_started_at: float,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    missing = [
        item["image_file"]
        for item in manifest["images"]
        if not (images_directory / item["image_file"]).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"missing extracted images: {', '.join(missing)}")

    first_image = load_image(images_directory / manifest["images"][0]["image_file"])
    first_input = preprocess(first_image, preprocessing_mode)
    first_prediction_ms: float | None = None
    process_start_to_first_prediction_ms: float | None = None
    for warmup_index in range(warmup_count):
        started = time.perf_counter()
        recognize(first_input)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if warmup_index == 0:
            first_prediction_ms = elapsed_ms
            process_start_to_first_prediction_ms = (
                time.perf_counter() - process_started_at
            ) * 1000

    records: list[dict[str, Any]] = []
    for item in manifest["images"]:
        raw_output = ""
        confidence: float | None = None
        error: str | None = None
        inference_ms: float | None = None
        preprocessing_ms: float | None = None
        total_started = time.perf_counter()
        try:
            preprocessing_started = time.perf_counter()
            image = load_image(images_directory / item["image_file"])
            prepared = preprocess(image, preprocessing_mode)
            preprocessing_ms = (time.perf_counter() - preprocessing_started) * 1000
            inference_started = time.perf_counter()
            raw_output, confidence = recognize(prepared)
            inference_ms = (time.perf_counter() - inference_started) * 1000
        except Exception as exception:  # Persist every per-image inference failure.
            error = f"{type(exception).__name__}: {exception}"
        total_ms = (time.perf_counter() - total_started) * 1000
        records.append(
            {
                "image_id": item["id"],
                "group_id": item["group_id"],
                "timestamp_seconds": item["timestamp_seconds"],
                "tags": item["tags"],
                "line_count": item["line_count"],
                "ground_truth": item["ground_truth"],
                "raw_output": raw_output,
                "normalized_output": normalize_text(raw_output),
                "confidence": confidence,
                "preprocessing_ms": preprocessing_ms,
                "inference_ms": inference_ms,
                "total_ms": total_ms,
                "error": error,
            }
        )

    sampler.stop()
    metadata: dict[str, Any] = {
        "engine": engine,
        "engine_version": engine_version,
        "model_id": model_id,
        "recognition_mode": "recognition_only_whole_crop",
        "device": "cpu",
        "batch_size": 1,
        "threads": threads,
        "warmup_count": warmup_count,
        "preprocessing": preprocessing_mode,
        "preprocessing_description": PREPROCESSING_DESCRIPTION[preprocessing_mode],
        "image_loading_in_total_ms": True,
        "image_loading_in_inference_ms": False,
        "initialization_ms": initialization_ms,
        "first_warmup_prediction_ms": first_prediction_ms,
        "script_entry_to_first_prediction_ms": process_start_to_first_prediction_ms,
        "memory_sampling_interval_ms": sampler.interval_seconds * 1000,
        "peak_parent_rss_bytes": sampler.peak_parent_rss_bytes,
        "peak_process_tree_rss_bytes": sampler.peak_tree_rss_bytes,
        "model_directory_bytes": directory_size(model_directory),
        "python_environment_bytes": directory_size(Path(sys.prefix)),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "installed_packages": installed_packages(),
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    result = {
        "schema_version": 1,
        "manifest_sha256": _sha256_text(manifest_path.read_text(encoding="utf-8")),
        "metadata": metadata,
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
