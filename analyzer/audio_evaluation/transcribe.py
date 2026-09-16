"""Transcribe extracted clips once with fixed Korean faster-whisper settings."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

import psutil
from faster_whisper import WhisperModel

from analyzer.audio_evaluation.core import (
    directory_size,
    load_json,
    save_json,
    sha256_file,
    source_time,
    validate_manifest,
)


def process_tree_rss_bytes(process: psutil.Process) -> int:
    processes = [process]
    try:
        processes.extend(process.children(recursive=True))
    except psutil.Error:
        pass
    total = 0
    for item in processes:
        try:
            total += item.memory_info().rss
        except psutil.Error:
            pass
    return total


class PeakRssSampler:
    def __init__(self, interval_seconds: float = 0.05):
        self.interval_seconds = interval_seconds
        self.process = psutil.Process()
        self.baseline = process_tree_rss_bytes(self.process)
        self.peak = self.baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.peak = max(self.peak, process_tree_rss_bytes(self.process))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, process_tree_rss_bytes(self.process))


def transcribe(manifest_path: Path, audio_dir: Path, model_dir: Path, output_path: Path) -> dict:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    extraction = load_json(audio_dir / "extraction.json")
    if extraction["manifest_sha256"] != sha256_file(manifest_path):
        raise ValueError("extraction was produced from a different manifest")
    extracted = {row["sample_id"]: row for row in extraction["rows"]}
    if set(extracted) != {sample["id"] for sample in manifest["samples"]}:
        raise ValueError("extraction sample set differs from manifest")

    os.environ.setdefault("OMP_NUM_THREADS", "8")
    sampler = PeakRssSampler()
    run_started = time.perf_counter()
    with sampler:
        init_started = time.perf_counter()
        model = WhisperModel(
            "large-v3-turbo",
            device="cpu",
            compute_type="int8",
            cpu_threads=8,
            num_workers=1,
            download_root=str(model_dir),
        )
        initialization_seconds = time.perf_counter() - init_started
        rows = []
        for sample in manifest["samples"]:
            extracted_row = extracted[sample["id"]]
            audio_path = audio_dir / extracted_row["audio_file"]
            if sha256_file(audio_path) != extracted_row["audio_sha256"]:
                raise ValueError(f"audio changed after extraction: {sample['id']}")
            inference_started = time.perf_counter()
            segments_generator, info = model.transcribe(
                str(audio_path),
                language="ko",
                task="transcribe",
                beam_size=5,
                word_timestamps=True,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=None,
            )
            segments = list(segments_generator)
            inference_seconds = time.perf_counter() - inference_started
            segment_rows = []
            for segment in segments:
                words = [
                    {
                        "clip_start_seconds": round(word.start, 3),
                        "clip_end_seconds": round(word.end, 3),
                        "source_start_seconds": source_time(sample["audio_start_seconds"], word.start),
                        "source_end_seconds": source_time(sample["audio_start_seconds"], word.end),
                        "word": word.word,
                        "probability": word.probability,
                    }
                    for word in (segment.words or [])
                ]
                segment_rows.append(
                    {
                        "clip_start_seconds": round(segment.start, 3),
                        "clip_end_seconds": round(segment.end, 3),
                        "source_start_seconds": source_time(sample["audio_start_seconds"], segment.start),
                        "source_end_seconds": source_time(sample["audio_start_seconds"], segment.end),
                        "text": segment.text,
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_prob": segment.no_speech_prob,
                        "temperature": segment.temperature,
                        "words": words,
                    }
                )
            rows.append(
                {
                    "sample_id": sample["id"],
                    "video_id": sample["video_id"],
                    "audio_start_seconds": sample["audio_start_seconds"],
                    "audio_end_seconds": sample["audio_end_seconds"],
                    "inference_seconds": inference_seconds,
                    "language": info.language,
                    "language_probability": info.language_probability,
                    "duration_seconds": info.duration,
                    "duration_after_vad_seconds": info.duration_after_vad,
                    "raw_text": "".join(segment.text for segment in segments),
                    "segments": segment_rows,
                }
            )
    total_seconds = time.perf_counter() - run_started
    result = {
        "schema_version": 1,
        "raw_output_note": "No reference subtitle, OCR output, hotword, prefix, or initial prompt was supplied to ASR.",
        "manifest_sha256": sha256_file(manifest_path),
        "extraction_sha256": sha256_file(audio_dir / "extraction.json"),
        "model": {
            "name": "large-v3-turbo",
            "runtime": "faster-whisper",
            "faster_whisper_version": importlib.metadata.version("faster-whisper"),
            "ctranslate2_version": importlib.metadata.version("ctranslate2"),
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 8,
            "num_workers": 1,
            "model_directory_bytes": directory_size(model_dir),
        },
        "settings": {
            "language": "ko",
            "task": "transcribe",
            "beam_size": 5,
            "word_timestamps": True,
            "vad_filter": False,
            "condition_on_previous_text": False,
            "initial_prompt": None,
        },
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "processor": platform.processor(),
            "logical_cpu_count": psutil.cpu_count(logical=True),
            "physical_cpu_count": psutil.cpu_count(logical=False),
            "total_memory_bytes": psutil.virtual_memory().total,
            "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True, encoding="utf-8").splitlines()[0],
        },
        "measurements": {
            "initialization_seconds": initialization_seconds,
            "total_process_seconds": total_seconds,
            "rss_sample_interval_seconds": sampler.interval_seconds,
            "baseline_process_tree_rss_bytes": sampler.baseline,
            "peak_process_tree_rss_bytes": sampler.peak,
        },
        "rows": rows,
    }
    save_json(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transcribe(args.manifest, args.audio_dir, args.model_dir, args.output)


if __name__ == "__main__":
    main()
