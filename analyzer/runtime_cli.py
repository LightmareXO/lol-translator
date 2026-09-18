"""Analyze a local video range with PaddleOCR and local Ollama translation."""

from __future__ import annotations

import argparse
import base64
from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.ocr_evaluation.preprocessing import preprocess
from analyzer.ocr_evaluation.run_paddle import MODEL_ID as PADDLE_MODEL_ID
from analyzer.ocr_evaluation.run_paddle import result_text_and_score
from analyzer.runtime_core import (
    PROJECT_KIND,
    SCHEMA_VERSION,
    apply_translation,
    atomic_write_json,
    effective_korean,
    migrate_project,
    read_json,
    resolve_analysis_range,
    is_low_information_ocr,
    validate_project,
    validate_request,
)
from analyzer.subtitle_detection import (
    DEFAULT_CONFIRMATION_SAMPLES,
    DEFAULT_SIMILARITY_THRESHOLD,
    DETECTION_VERSION,
    DetectedInterval,
    LineIntervalTracker,
    extract_text_feature,
    line_slices,
)
from analyzer.translation_evaluation.evaluate import (
    LocalClient,
    chat_with_retry,
    make_payload,
    validate_thinking,
)


TRANSLATION_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
OCR_THREADS = 4
OCR_PREPROCESSING = "contrast"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SHOWINFO_PTS_PATTERN = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([-+0-9.eE]+)")


class Cancelled(Exception):
    pass


def sha256_file(path: Path, cancelled: Callable[[], bool]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            if cancelled():
                raise Cancelled()
            digest.update(block)
    return digest.hexdigest()


def executable(name: str, environment_name: str) -> str:
    configured = os.environ.get(environment_name)
    if configured:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError(
                f"{environment_name}には、存在する実行ファイルの絶対パスを指定してください。"
            )
        return str(path)
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(
            f"{name}が見つかりません。PATHへ追加するか、対応する環境変数で絶対パスを指定してください。"
        )
    return resolved


def probe_video(video: Path) -> dict[str, Any]:
    ffprobe = executable("ffprobe", "LOL_TRANSLATOR_FFPROBE")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration,start_time",
        "-of",
        "json",
        str(video),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobeで動画を読み込めませんでした。動画が破損していないか確認してください: {result.stderr.strip()}"
        )
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        duration = float(payload["format"]["duration"])
        start_time = float(payload["format"].get("start_time", 0) or 0)
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("動画の幅、高さ、再生時間を取得できませんでした。") from error
    if (
        not math.isfinite(duration)
        or duration <= 0
        or not math.isfinite(start_time)
        or width <= 0
        or height <= 0
    ):
        raise RuntimeError("動画の幅、高さ、または再生時間が不正です。")
    return {
        "duration_seconds": duration,
        "start_time_seconds": start_time,
        "width": width,
        "height": height,
    }


def _crop_pixels(region: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    # Most source videos use 4:2:0 chroma.  Even crop coordinates and sizes
    # prevent FFmpeg from silently rounding the raw-video dimensions.
    x = max(0, min(width - 2, round(region["x"] * width)))
    y = max(0, min(height - 2, round(region["y"] * height)))
    x -= x % 2
    y -= y % 2
    right = max(x + 2, min(width, round((region["x"] + region["width"]) * width)))
    bottom = max(y + 2, min(height, round((region["y"] + region["height"]) * height)))
    crop_width = right - x
    crop_height = bottom - y
    crop_width -= crop_width % 2
    crop_height -= crop_height % 2
    return x, y, max(2, crop_width), max(2, crop_height)


@dataclass(frozen=True)
class DecodedFrame:
    timestamp_seconds: float
    source_pts_seconds: float
    source_index: int
    image: Any


def stream_frames(
    video: Path,
    *,
    start: float,
    end: float,
    interval_ms: int,
    region: dict[str, float],
    video_width: int,
    video_height: int,
    source_start_time: float,
    progress: Callable[[float, str], None],
    cancelled: Callable[[], bool],
) -> Iterator[DecodedFrame]:
    """Decode ROI frames incrementally and retain FFmpeg-derived PTS."""
    import numpy as np

    ffmpeg = executable("ffmpeg", "LOL_TRANSLATOR_FFMPEG")
    x, y, width, height = _crop_pixels(region, video_width, video_height)
    duration = end - start
    fps = 1000 / interval_ms
    absolute_start = start + source_start_time
    absolute_end = end + source_start_time
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostdin",
        "-ss",
        f"{absolute_start:.6f}",
        "-copyts",
        "-i",
        str(video),
        "-to",
        f"{absolute_end:.6f}",
        "-an",
        "-vf",
        f"crop={width}:{height}:{x}:{y},fps={fps:.12g},showinfo",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "-nostats",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    assert process.stdout is not None and process.stderr is not None
    pts_values: queue.Queue[float] = queue.Queue()
    stderr_tail: deque[str] = deque(maxlen=40)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw_line in iter(process.stderr.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            match = SHOWINFO_PTS_PATTERN.search(line)
            if match:
                try:
                    pts_values.put(float(match.group(1)))
                except ValueError:
                    stderr_tail.append(line)
            elif line:
                stderr_tail.append(line)

    reader = threading.Thread(target=read_stderr, name="ffmpeg-stderr", daemon=True)
    reader.start()
    frame_size = width * height * 3
    index = 0
    try:
        while True:
            if cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise Cancelled()
            data = bytearray()
            while len(data) < frame_size:
                chunk = process.stdout.read(frame_size - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if not data:
                break
            if len(data) != frame_size:
                raise RuntimeError("ffmpegから不完全なフレームを受信しました。")
            try:
                source_pts = pts_values.get(timeout=5)
            except queue.Empty as error:
                raise RuntimeError("ffmpegからフレームのPTSを取得できませんでした。") from error
            timestamp = source_pts - source_start_time
            if timestamp < start - interval_ms / 1000 - 1e-6:
                continue
            if timestamp >= end - 1e-6:
                # Keep draining stdout so FFmpeg cannot block on a full pipe.
                # The half-open analysis range still excludes this frame.
                continue
            image = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3)).copy()
            progress(
                min(1.0, max(0.0, (timestamp - start) / duration)),
                "PTS付きROIフレームを走査中",
            )
            yield DecodedFrame(timestamp, source_pts, index, image)
            index += 1
        process.wait(timeout=10)
        reader.join(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(
                "ffmpegで指定区間を走査できませんでした: "
                + "\n".join(stderr_tail)
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
    if index == 0:
        raise RuntimeError("指定区間から画像を1枚も抽出できませんでした。")
    progress(1.0, f"{index}枚のPTS付きフレームを走査")


class PaddleRecognizer:
    def __init__(self, model_directory: Path):
        if not model_directory.is_dir() or not any(model_directory.rglob("*")):
            raise RuntimeError(
                "PaddleOCRモデルが見つかりません。LOL_TRANSLATOR_PADDLE_MODEL_DIRにモデルディレクトリの絶対パスを指定してください。"
            )
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(model_directory.resolve())
        os.environ["PADDLE_PDX_MODEL_SOURCE"] = "bos"
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ["OMP_NUM_THREADS"] = str(OCR_THREADS)
        os.environ["MKL_NUM_THREADS"] = str(OCR_THREADS)
        try:
            from paddleocr import TextRecognition
        except ImportError as error:
            raise RuntimeError(
                "選択したPython環境にPaddleOCRがありません。READMEの依存関係を導入してください。"
            ) from error
        self.model = TextRecognition(
            model_name=PADDLE_MODEL_ID,
            device="cpu",
            engine="paddle_static",
            enable_hpi=False,
            enable_mkldnn=True,
            cpu_threads=OCR_THREADS,
        )

    def _one(self, image: Any) -> tuple[str, float | None]:
        results = list(self.model.predict(input=image, batch_size=1))
        if len(results) != 1:
            raise RuntimeError(
                f"PaddleOCRが1枚の画像に対して{len(results)}件の結果を返しました。"
            )
        return result_text_and_score(results[0])

    def recognize(self, image: Any) -> tuple[str, float | None]:
        return self._one(preprocess(image, OCR_PREPROCESSING))


class Translator:
    def __init__(self, root: Path):
        self.model = TRANSLATION_MODEL
        self.prompts = read_json(root / "translation_evaluation" / "prompts-instruct.json")
        self.glossary = read_json(root / "translation_evaluation" / "glossary.json")
        self.client = LocalClient(timeout=60)
        try:
            models = self.client.request("tags").get("models", [])
            info = next((item for item in models if item.get("name") == self.model), None)
            if info is None:
                raise RuntimeError(
                    f"Ollamaモデル「{self.model}」がありません。ollama pull {self.model}を実行してください。"
                )
            show = self.client.request("show", {"model": self.model})
            if show.get("remote_host") or show.get("remote_model"):
                raise RuntimeError("このアプリではリモートのOllamaモデルを使用できません。")
            validate_thinking(show, self.prompts)
            self.identity = {
                "model": self.model,
                "digest": info.get("digest"),
                "size_bytes": info.get("size"),
                "details": info.get("details"),
                "ollama": self.client.request("version"),
            }
        except RuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError, socket.timeout, ValueError, KeyError) as error:
            raise RuntimeError(
                "Ollamaへ接続できません。Ollamaを起動してから、もう一度お試しください。"
            ) from error

    def translate(self, text: str) -> str:
        payload = make_payload(
            self.model,
            text,
            True,
            self.prompts,
            self.glossary,
            glossary_filter_text=text,
        )
        result = chat_with_retry(self.client, payload, attempts=2)
        if result["status"] != "ok":
            error_types = ", ".join(item["type"] for item in result["errors"])
            raise RuntimeError(
                f"Ollamaで翻訳できませんでした: {error_types or '原因不明'}"
            )
        return result["response"]["message"]["content"].strip()

    def configuration(self) -> dict[str, Any]:
        return {
            **self.identity,
            "prompt_version": self.prompts["version"],
            "prompt": self.prompts["qwen3"],
            "options": self.prompts["options"],
            "think": self.prompts["qwen_think"],
            "glossary_version": self.glossary["version"],
            "glossary_enabled": True,
            "glossary_mode": "relevant-source-terms-v1",
        }


def _model_directory(root: Path) -> Path:
    configured = os.environ.get("LOL_TRANSLATOR_PADDLE_MODEL_DIR")
    return Path(configured) if configured else root / "ocr_evaluation" / "models" / "paddle"


def _encode_png(image: Any) -> str:
    import cv2

    succeeded, encoded = cv2.imencode(".png", image)
    if not succeeded:
        raise RuntimeError("代表フレームをPNGへ変換できませんでした。")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _recognize_interval(
    interval: DetectedInterval, recognizer: PaddleRecognizer
) -> tuple[dict[str, Any], Any, int]:
    attempts: list[dict[str, Any]] = []
    accepted_feature = None
    accepted_text = ""
    accepted_confidence: float | None = None
    for rank, feature in enumerate(interval.representative_candidates[:3], start=1):
        try:
            text, confidence = recognizer.recognize(feature.image)
            text = text.strip()
            accepted = bool(text) and not is_low_information_ocr(text, confidence)
            attempts.append(
                {
                    "attempt": rank,
                    "candidate_timestamp_seconds": feature.timestamp_seconds,
                    "candidate_source_pts_seconds": feature.source_pts_seconds,
                    "raw_text": text,
                    "confidence": confidence,
                    "accepted": accepted,
                    "rejection_reason": (
                        None
                        if accepted
                        else ("empty_ocr" if not text else "low_information_ocr")
                    ),
                    "error": None,
                }
            )
            if accepted:
                accepted_feature = feature
                accepted_text = text
                accepted_confidence = confidence
                break
        except Exception as error:
            attempts.append(
                {
                    "attempt": rank,
                    "candidate_timestamp_seconds": feature.timestamp_seconds,
                    "candidate_source_pts_seconds": feature.source_pts_seconds,
                    "raw_text": "",
                    "confidence": None,
                    "accepted": False,
                    "rejection_reason": "ocr_exception",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    if accepted_feature is None:
        fallback = max(
            attempts,
            key=lambda item: (
                item["confidence"] if item["confidence"] is not None else -1,
                len(item["raw_text"]),
            ),
            default=None,
        )
        accepted_feature = interval.representative_candidates[0]
        raw_text = fallback["raw_text"] if fallback else ""
        confidence = fallback["confidence"] if fallback else None
        error = "文字候補区間を検出しましたが、採用できるOCR結果がありませんでした。"
        return (
            {
                "status": "error",
                "raw_text": raw_text,
                "raw_lines": [raw_text] if raw_text else [],
                "confidence": confidence,
                "attempts": attempts,
                "error": error,
            },
            accepted_feature.image,
            len(attempts),
        )
    return (
        {
            "status": "completed",
            "raw_text": accepted_text,
            "raw_lines": [accepted_text],
            "confidence": accepted_confidence,
            "attempts": attempts,
            "error": None,
        },
        accepted_feature.image,
        len(attempts),
    )


def _simultaneous_relationships(subtitles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for index, left in enumerate(subtitles):
        for right in subtitles[index + 1 :]:
            if left["line_id"] == right["line_id"]:
                continue
            overlap_start = max(left["start_seconds"], right["start_seconds"])
            overlap_end = min(left["end_seconds"], right["end_seconds"])
            if overlap_end > overlap_start + 1e-9:
                relationships.append(
                    {
                        "type": "simultaneous",
                        "event_ids": [left["id"], right["id"]],
                        "start_seconds": overlap_start,
                        "end_seconds": overlap_end,
                    }
                )
    return relationships


def analyze(
    request_path: Path,
    progress_path: Path,
    result_path: Path,
    cancel_path: Path,
    work_directory: Path,
) -> None:
    del work_directory  # Streaming detection does not persist every sampled frame.
    overall_started = time.perf_counter()
    analyzer_root = Path(__file__).resolve().parent
    request = validate_request(read_json(request_path))
    video = Path(request["video_path"])
    timings_ms: dict[str, int] = {}
    peak_rss_bytes = 0

    try:
        import psutil

        runtime_process = psutil.Process()

        def record_memory() -> None:
            nonlocal peak_rss_bytes
            peak_rss_bytes = max(peak_rss_bytes, runtime_process.memory_info().rss)

    except ImportError:

        def record_memory() -> None:
            return

    def cancelled() -> bool:
        return cancel_path.exists()

    def update(phase: str, current: int, total: int, message: str) -> None:
        atomic_write_json(
            progress_path,
            {
                "state": "running",
                "phase": phase,
                "current": current,
                "total": total,
                "message": message,
                "error": None,
            },
        )

    update("probing", 0, 1, "動画情報を確認中")
    phase_started = time.perf_counter()
    metadata = probe_video(video)
    start, end = resolve_analysis_range(request, metadata["duration_seconds"])
    file_hash = sha256_file(video, cancelled)
    timings_ms["probe_and_hash"] = round((time.perf_counter() - phase_started) * 1000)
    record_memory()
    if cancelled():
        raise Cancelled()

    minimum_duration_seconds = (
        request["settings"]["minimum_display_duration_ms"] / 1000
    )
    roi_height = _crop_pixels(
        request["subtitle_region"], metadata["width"], metadata["height"]
    )[3]
    line_regions = line_slices(
        roi_height, request["settings"]["line_split_ratio"]
    )
    trackers = {
        line_id: LineIntervalTracker(
            line_id=line_id,
            line_index=line_index,
            minimum_duration_seconds=minimum_duration_seconds,
            similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
            confirmation_samples=DEFAULT_CONFIRMATION_SAMPLES,
        )
        for line_id, line_index, _, _ in line_regions
    }
    frame_count = 0
    feature_time = 0.0
    scan_started = time.perf_counter()
    for frame in stream_frames(
        video,
        start=start,
        end=end,
        interval_ms=request["settings"]["sample_interval_ms"],
        region=request["subtitle_region"],
        video_width=metadata["width"],
        video_height=metadata["height"],
        source_start_time=metadata["start_time_seconds"],
        progress=lambda fraction, message: update(
            "detecting", round(fraction * 1000), 1000, message
        ),
        cancelled=cancelled,
    ):
        if cancelled():
            raise Cancelled()
        frame_count += 1
        for line_id, line_index, top, bottom in line_regions:
            feature_started = time.perf_counter()
            feature = extract_text_feature(
                frame.image[top:bottom, :],
                timestamp_seconds=frame.timestamp_seconds,
                source_pts_seconds=frame.source_pts_seconds,
                source_index=frame.source_index,
                line_id=line_id,
                line_index=line_index,
            )
            feature_time += time.perf_counter() - feature_started
            trackers[line_id].add(feature)
        if frame_count % 10 == 0:
            record_memory()
    scan_elapsed = time.perf_counter() - scan_started
    for tracker in trackers.values():
        tracker.finish(end)
    intervals = sorted(
        (item for tracker in trackers.values() for item in tracker.intervals),
        key=lambda item: (item.start_seconds, item.line_index, item.end_seconds),
    )
    dropped = sorted(
        (item for tracker in trackers.values() for item in tracker.dropped),
        key=lambda item: (item.start_seconds, item.line_index, item.end_seconds),
    )
    timings_ms["frame_decoding"] = round(max(0.0, scan_elapsed - feature_time) * 1000)
    timings_ms["image_detection"] = round(feature_time * 1000)
    record_memory()

    update("preflight", 0, 1, "PaddleOCRモデルを確認中")
    phase_started = time.perf_counter()
    recognizer = PaddleRecognizer(_model_directory(analyzer_root))
    timings_ms["ocr_model_startup"] = round((time.perf_counter() - phase_started) * 1000)
    update("preflight", 1, 1, "PaddleOCRモデルを確認しました")

    subtitles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    ocr_call_count = 0
    ocr_started = time.perf_counter()
    for index, interval in enumerate(intervals, start=1):
        if cancelled():
            raise Cancelled()
        ocr, selected_image, calls = _recognize_interval(interval, recognizer)
        ocr_call_count += calls
        identifier = f"subtitle-{index:05d}"
        if ocr["status"] == "error":
            errors.append(
                {
                    "phase": "ocr",
                    "subtitle_id": identifier,
                    "line_id": interval.line_id,
                    "timestamp_seconds": interval.representative_timestamp_seconds,
                    "message": ocr["error"],
                }
            )
        source_text = ocr["raw_text"]
        subtitles.append(
            {
                "id": identifier,
                "line_id": interval.line_id,
                "line_index": interval.line_index,
                "start_seconds": interval.start_seconds,
                "end_seconds": interval.end_seconds,
                "image_png_base64": _encode_png(selected_image),
                "detection": {
                    "status": "needs_review" if interval.needs_review else "confirmed",
                    "start_reason": interval.start_reason,
                    "end_reason": interval.end_reason,
                    "needs_review": interval.needs_review,
                    "sample_count": interval.sample_count,
                    "median_similarity": interval.median_similarity,
                    "representative_timestamp_seconds": interval.representative_timestamp_seconds,
                    "representative_source_pts_seconds": (
                        interval.representative_timestamp_seconds
                        + metadata["start_time_seconds"]
                    ),
                    "representative_score": interval.representative_score,
                    "representative_reason": "sharpness_text_completeness_and_stability",
                    "candidate_timestamps_seconds": [
                        feature.timestamp_seconds
                        for feature in interval.representative_candidates
                    ],
                    "source_pts_start_seconds": (
                        interval.start_seconds + metadata["start_time_seconds"]
                    ),
                    "source_pts_end_seconds": (
                        interval.end_seconds + metadata["start_time_seconds"]
                    ),
                },
                "ocr": ocr,
                "corrected_ko": None,
                "translation": {
                    "status": "pending" if ocr["status"] == "completed" else "error",
                    "source_ko": source_text,
                    "generated_ja": None,
                    "user_ja": None,
                    "error": None if ocr["status"] == "completed" else ocr["error"],
                },
            }
        )
        update("recognizing", index, len(intervals), f"区間OCR {index}/{len(intervals)}")
    timings_ms["ocr"] = round((time.perf_counter() - ocr_started) * 1000)
    record_memory()

    translatable = [
        subtitle for subtitle in subtitles if subtitle["ocr"]["status"] == "completed"
    ]
    translator: Translator | None = None
    translation_configuration: dict[str, Any]
    if translatable:
        update("preflight", 0, 1, "Ollamaモデルを確認中")
        phase_started = time.perf_counter()
        try:
            translator = Translator(analyzer_root)
            translation_configuration = translator.configuration()
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            translation_configuration = {
                "model": TRANSLATION_MODEL,
                "status": "unavailable",
                "error": message,
            }
            errors.append({"phase": "translation_preflight", "message": message})
        timings_ms["translation_model_startup"] = round(
            (time.perf_counter() - phase_started) * 1000
        )
    else:
        translation_configuration = {
            "model": TRANSLATION_MODEL,
            "status": "not_started_no_accepted_ocr",
        }

    translation_call_count = 0
    translation_started = time.perf_counter()
    for index, subtitle in enumerate(subtitles):
        if cancelled():
            raise Cancelled()
        if subtitle["ocr"]["status"] != "completed":
            continue
        source = effective_korean(subtitle)
        if translator is None:
            message = translation_configuration.get(
                "error", "Ollamaモデルを利用できません。"
            )
            subtitle["translation"].update({"status": "error", "error": message})
            continue
        try:
            translation_call_count += 1
            apply_translation(subtitle, translator.translate(source), source_ko=source)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            subtitle["translation"].update({"status": "error", "error": message})
            errors.append(
                {"phase": "translation", "subtitle_id": subtitle["id"], "message": message}
            )
        update(
            "translating",
            index + 1,
            len(subtitles),
            f"区間翻訳 {index + 1}/{len(subtitles)}",
        )
    timings_ms["translation"] = round((time.perf_counter() - translation_started) * 1000)
    record_memory()

    stat = video.stat()
    project = {
        "schema_version": SCHEMA_VERSION,
        "kind": PROJECT_KIND,
        "source_video": {
            "path": str(video),
            "name": video.name,
            "size_bytes": stat.st_size,
            "modified_unix_ms": round(stat.st_mtime * 1000),
            "sha256": file_hash,
            **metadata,
        },
        "analysis": {
            "mode": request["analysis_range"]["mode"],
            "start_seconds": start,
            "end_seconds": end,
            "subtitle_region": request["subtitle_region"],
            **request["settings"],
        },
        "configuration": {
            "detection": {
                "version": DETECTION_VERSION,
                "boundary_source": "image_only",
                "uses_ocr_for_boundaries": False,
                "time_source": "ffmpeg_filter_pts",
                "sample_interval_ms": request["settings"]["sample_interval_ms"],
                "similarity_threshold": DEFAULT_SIMILARITY_THRESHOLD,
                "confirmation_samples": DEFAULT_CONFIRMATION_SAMPLES,
                "minimum_display_duration_ms": request["settings"]["minimum_display_duration_ms"],
                "colors": ["white", "yellow"],
                "line_regions": [
                    {
                        "line_id": line_id,
                        "line_index": line_index,
                        "relative_y_start": top / max(1, roi_height),
                        "relative_y_end": bottom / max(1, roi_height),
                    }
                    for line_id, line_index, top, bottom in line_regions
                ],
            },
            "ocr": {
                "engine": "PaddleOCR",
                "paddleocr_version": importlib.metadata.version("paddleocr"),
                "paddlepaddle_version": importlib.metadata.version("paddlepaddle"),
                "model": PADDLE_MODEL_ID,
                "device": "cpu",
                "threads": OCR_THREADS,
                "preprocessing": OCR_PREPROCESSING,
                "low_information_filter": {
                    "version": "ocr-confidence-v1",
                    "minimum_confidence": 0.5,
                    "minimum_non_korean_confidence": 0.6,
                    "minimum_non_korean_alphanumeric_length": 2,
                },
                "maximum_attempts_per_interval": 3,
                "confidence_is_ground_truth": False,
            },
            "translation": translation_configuration,
        },
        "subtitles": subtitles,
        "relationships": {"simultaneous": _simultaneous_relationships(subtitles)},
        "processing": {
            "state": "completed_with_errors" if errors else "completed",
            "sample_count": frame_count,
            "line_observation_count": frame_count * len(line_regions),
            "detection_count": len(intervals),
            "subtitle_count": len(subtitles),
            "dropped_intervals": [asdict(item) for item in dropped],
            "ocr_call_count": ocr_call_count,
            "translation_call_count": translation_call_count,
            "timings_ms": timings_ms,
            "peak_rss_bytes": peak_rss_bytes,
            "result_json_bytes": 0,
            "errors": errors,
        },
    }
    timings_ms["total"] = round((time.perf_counter() - overall_started) * 1000)
    for _ in range(3):
        encoded_size = len(
            (json.dumps(project, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
                "utf-8"
            )
        )
        if project["processing"]["result_json_bytes"] == encoded_size:
            break
        project["processing"]["result_json_bytes"] = encoded_size
    validate_project(project)
    update("saving", 0, 1, "解析結果を保存中")
    atomic_write_json(result_path, project)
    atomic_write_json(
        progress_path,
        {
            "state": "completed",
            "phase": "completed",
            "current": 1,
            "total": 1,
            "message": f"{len(subtitles)}件の字幕を作成しました",
            "error": None,
        },
    )


def retranslate_project(
    project_path: Path, progress_path: Path, result_path: Path, cancel_path: Path
) -> None:
    analyzer_root = Path(__file__).resolve().parent
    project = migrate_project(read_json(project_path))
    validate_project(project)
    translator = Translator(analyzer_root)
    subtitles = project["subtitles"]
    errors: list[dict[str, Any]] = []
    translation_call_count = 0
    for index, subtitle in enumerate(subtitles):
        if cancel_path.exists():
            raise Cancelled()
        source = effective_korean(subtitle)
        if not source.strip():
            message = "OCR原文または修正後の韓国語が空のため、翻訳できません。"
            subtitle["translation"].update({"status": "error", "error": message})
            errors.append(
                {"phase": "translation", "subtitle_id": subtitle["id"], "message": message}
            )
            continue
        try:
            translation_call_count += 1
            apply_translation(subtitle, translator.translate(source), source_ko=source)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            subtitle["translation"].update({"status": "error", "error": message})
            errors.append(
                {"phase": "translation", "subtitle_id": subtitle["id"], "message": message}
            )
        atomic_write_json(
            progress_path,
            {
                "state": "running",
                "phase": "translating",
                "current": index + 1,
                "total": len(subtitles),
                "message": f"翻訳 {index + 1}/{len(subtitles)}",
                "error": None,
            },
        )
    project["configuration"]["translation"] = translator.configuration()
    project["processing"]["state"] = "completed_with_errors" if errors else "completed"
    project["processing"]["errors"] = [
        item for item in project["processing"].get("errors", []) if item.get("phase") != "translation"
    ] + errors
    project["processing"]["translation_call_count"] = translation_call_count
    atomic_write_json(result_path, project)
    atomic_write_json(
        progress_path,
        {
            "state": "completed",
            "phase": "completed",
            "current": len(subtitles),
            "total": len(subtitles),
            "message": "保存済みOCRから翻訳を再実行しました",
            "error": None,
        },
    )


def translate_one(text: str) -> dict[str, Any]:
    translator = Translator(Path(__file__).resolve().parent)
    return {
        "source_ko": text,
        "generated_ja": translator.translate(text),
        "configuration": translator.configuration(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--request", type=Path, required=True)
    analyze_parser.add_argument("--progress", type=Path, required=True)
    analyze_parser.add_argument("--result", type=Path, required=True)
    analyze_parser.add_argument("--cancel", type=Path, required=True)
    analyze_parser.add_argument("--work-dir", type=Path, required=True)
    retranslate_parser = subparsers.add_parser("retranslate")
    retranslate_parser.add_argument("--project", type=Path, required=True)
    retranslate_parser.add_argument("--progress", type=Path, required=True)
    retranslate_parser.add_argument("--result", type=Path, required=True)
    retranslate_parser.add_argument("--cancel", type=Path, required=True)
    one_parser = subparsers.add_parser("translate-one")
    one_parser.add_argument("--text", required=True)
    args = parser.parse_args()
    progress = getattr(args, "progress", None)
    try:
        if args.command == "analyze":
            analyze(args.request, args.progress, args.result, args.cancel, args.work_dir)
        elif args.command == "retranslate":
            retranslate_project(args.project, args.progress, args.result, args.cancel)
        else:
            print(json.dumps(translate_one(args.text), ensure_ascii=False, allow_nan=False))
        return 0
    except Cancelled:
        if progress:
            atomic_write_json(
                progress,
                {
                    "state": "cancelled",
                    "phase": "cancelled",
                    "current": 0,
                    "total": 0,
                    "message": "解析をキャンセルしました",
                    "error": None,
                },
            )
        return 2
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if progress:
            atomic_write_json(
                progress,
                {
                    "state": "failed",
                    "phase": "failed",
                    "current": 0,
                    "total": 0,
                    "message": "解析に失敗しました",
                    "error": message,
                },
            )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
