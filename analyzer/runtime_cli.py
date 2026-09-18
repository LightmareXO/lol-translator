"""Analyze a local video range with PaddleOCR and local Ollama translation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.ocr_evaluation.preprocessing import load_image, preprocess
from analyzer.ocr_evaluation.run_paddle import MODEL_ID as PADDLE_MODEL_ID
from analyzer.ocr_evaluation.run_paddle import result_text_and_score
from analyzer.runtime_core import (
    PROJECT_KIND,
    SCHEMA_VERSION,
    Sample,
    apply_translation,
    atomic_write_json,
    effective_korean,
    merge_samples,
    read_json,
    resolve_analysis_range,
    is_low_information_ocr,
    stabilize_subtitles,
    validate_project,
    validate_request,
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
        "stream=width,height:format=duration",
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
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("動画の幅、高さ、再生時間を取得できませんでした。") from error
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("動画の幅、高さ、または再生時間が不正です。")
    return {"duration_seconds": duration, "width": width, "height": height}


def _crop_pixels(region: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    x = max(0, min(width - 1, round(region["x"] * width)))
    y = max(0, min(height - 1, round(region["y"] * height)))
    right = max(x + 1, min(width, round((region["x"] + region["width"]) * width)))
    bottom = max(y + 1, min(height, round((region["y"] + region["height"]) * height)))
    return x, y, right - x, bottom - y


def extract_frames(
    video: Path,
    destination: Path,
    *,
    start: float,
    end: float,
    interval_ms: int,
    region: dict[str, float],
    video_width: int,
    video_height: int,
    progress: Callable[[float, str], None],
    cancelled: Callable[[], bool],
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    ffmpeg = executable("ffmpeg", "LOL_TRANSLATOR_FFMPEG")
    x, y, width, height = _crop_pixels(region, video_width, video_height)
    duration = end - start
    fps = 1000 / interval_ms
    output = destination / "frame-%09d.png"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(video),
        "-t",
        f"{duration:.6f}",
        "-vf",
        f"crop={width}:{height}:{x}:{y},fps={fps:.12g}",
        "-start_number",
        "0",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(output),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    assert process.stdout is not None
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
            line = process.stdout.readline()
            if line:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        # FFmpeg currently reports microseconds for both keys.
                        elapsed = float(value) / 1_000_000
                        progress(min(1.0, max(0.0, elapsed / duration)), "指定区間のフレームを抽出中")
                    except ValueError:
                        pass
            if process.poll() is not None:
                break
            if not line:
                time.sleep(0.05)
        stderr = process.stderr.read() if process.stderr else ""
        if process.returncode != 0:
            raise RuntimeError(
                f"ffmpegで指定区間の画像を抽出できませんでした: {stderr.strip()}"
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    frames = sorted(destination.glob("frame-*.png"))
    if not frames:
        raise RuntimeError("指定区間から画像を1枚も抽出できませんでした。")
    progress(1.0, f"{len(frames)}枚のフレームを抽出")
    return frames


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

    def recognize(self, path: Path, line_split_ratio: float | None) -> tuple[str, tuple[str, ...], float | None]:
        image = preprocess(load_image(path), OCR_PREPROCESSING)
        images = [image]
        if line_split_ratio is not None:
            split = max(1, min(image.shape[0] - 1, round(image.shape[0] * line_split_ratio)))
            images = [image[:split, :], image[split:, :]]
        outputs = [self._one(part) for part in images]
        accepted = [
            (text.strip(), score)
            for text, score in outputs
            if text.strip() and not is_low_information_ocr(text, score)
        ]
        lines = tuple(text for text, _ in accepted)
        scores = [score for _, score in accepted if score is not None]
        return "\n".join(lines), lines, sum(scores) / len(scores) if scores else None


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


def analyze(
    request_path: Path,
    progress_path: Path,
    result_path: Path,
    cancel_path: Path,
    work_directory: Path,
) -> None:
    analyzer_root = Path(__file__).resolve().parent
    request = validate_request(read_json(request_path))
    video = Path(request["video_path"])

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
    metadata = probe_video(video)
    start, end = resolve_analysis_range(request, metadata["duration_seconds"])
    file_hash = sha256_file(video, cancelled)
    if cancelled():
        raise Cancelled()

    update("preflight", 0, 2, "OCRモデルとOllamaを確認中")
    recognizer = PaddleRecognizer(_model_directory(analyzer_root))
    update("preflight", 1, 2, "Ollamaモデルを確認中")
    translator = Translator(analyzer_root)
    update("preflight", 2, 2, "実行条件を確認しました")

    frames_directory = work_directory / "frames"
    frames = extract_frames(
        video,
        frames_directory,
        start=start,
        end=end,
        interval_ms=request["settings"]["sample_interval_ms"],
        region=request["subtitle_region"],
        video_width=metadata["width"],
        video_height=metadata["height"],
        progress=lambda fraction, message: update("extracting", round(fraction * 1000), 1000, message),
        cancelled=cancelled,
    )
    interval_seconds = request["settings"]["sample_interval_ms"] / 1000
    samples: list[Sample] = []
    for index, frame in enumerate(frames):
        if cancelled():
            raise Cancelled()
        timestamp = min(start + index * interval_seconds, end)
        try:
            text, lines, confidence = recognizer.recognize(
                frame, request["settings"]["line_split_ratio"]
            )
            status = "subtitle" if text.strip() else "no_subtitle"
            image = base64.b64encode(frame.read_bytes()).decode("ascii") if status == "subtitle" else None
            sample = Sample(timestamp, status, text, lines, confidence, image)
        except Exception as error:  # Preserve the timestamp and keep later samples usable.
            sample = Sample(timestamp, "ocr_error", error=f"{type(error).__name__}: {error}")
        samples.append(sample)
        update("recognizing", index + 1, len(frames), f"OCR {index + 1}/{len(frames)}")

    subtitles, errors = merge_samples(
        samples, interval_seconds=interval_seconds, range_end_seconds=end
    )
    subtitles = stabilize_subtitles(
        subtitles,
        maximum_gap_seconds=interval_seconds,
    )
    for index, subtitle in enumerate(subtitles):
        if cancelled():
            raise Cancelled()
        source = effective_korean(subtitle)
        try:
            apply_translation(subtitle, translator.translate(source), source_ko=source)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            subtitle["translation"].update({"status": "error", "error": message})
            errors.append(
                {"phase": "translation", "subtitle_id": subtitle["id"], "message": message}
            )
        update("translating", index + 1, len(subtitles), f"翻訳 {index + 1}/{len(subtitles)}")

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
                "timeline_stabilization": {
                    "version": "temporal-hangul-debounce-v2",
                    "maximum_gap_ms": request["settings"]["sample_interval_ms"],
                    "maximum_hangul_edit_ratio": 0.45,
                    "minimum_duration_ms": 1200,
                    "protect_conflicting_numbers_and_skill_letters": True,
                },
            },
            "translation": translator.configuration(),
        },
        "subtitles": subtitles,
        "processing": {
            "state": "completed_with_errors" if errors else "completed",
            "sample_count": len(samples),
            "subtitle_count": len(subtitles),
            "errors": errors,
        },
    }
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
    project = validate_project(read_json(project_path))
    translator = Translator(analyzer_root)
    subtitles = project["subtitles"]
    errors: list[dict[str, Any]] = []
    for index, subtitle in enumerate(subtitles):
        if cancel_path.exists():
            raise Cancelled()
        source = effective_korean(subtitle)
        try:
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
