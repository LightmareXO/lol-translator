"""Run PP-OCRv5 Korean recognition in an isolated CPU process."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

PROCESS_ENTRY_AT = time.perf_counter()

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.core import PREPROCESSING_MODES  # type: ignore
    from analyzer.ocr_evaluation.runner_common import (  # type: ignore
        MemorySampler,
        run_evaluation,
    )
else:
    from .core import PREPROCESSING_MODES
    from .runner_common import MemorySampler, run_evaluation


MODEL_ID = "korean_PP-OCRv5_mobile_rec"


def result_text_and_score(result: Any) -> tuple[str, float | None]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict) and "res" in payload:
        payload = payload["res"]
    if not isinstance(payload, dict) or "rec_text" not in payload:
        raise ValueError(f"unexpected PaddleOCR result: {type(result).__name__}")
    score = payload.get("rec_score")
    return str(payload["rec_text"]), float(score) if score is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--preprocessing", choices=PREPROCESSING_MODES, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()
    if args.threads <= 0 or args.warmup <= 0:
        parser.error("--threads and --warmup must be positive")

    args.model_dir.mkdir(parents=True, exist_ok=True)
    model_present_before = any(args.model_dir.rglob("*"))
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(args.model_dir.resolve())
    os.environ["PADDLE_PDX_MODEL_SOURCE"] = "bos"
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    os.environ["MKL_NUM_THREADS"] = str(args.threads)
    sampler = MemorySampler()
    sampler.start()
    try:
        from paddleocr import TextRecognition

        initialization_started = time.perf_counter()
        model = TextRecognition(
            model_name=MODEL_ID,
            device="cpu",
            engine="paddle_static",
            enable_hpi=False,
            enable_mkldnn=True,
            cpu_threads=args.threads,
        )
        initialization_ms = (time.perf_counter() - initialization_started) * 1000

        def recognize(image: Any) -> tuple[str, float | None]:
            results = list(model.predict(input=image, batch_size=1))
            if len(results) != 1:
                raise ValueError(f"expected one PaddleOCR result, got {len(results)}")
            return result_text_and_score(results[0])

        run_evaluation(
            engine="paddleocr",
            model_id=MODEL_ID,
            engine_version=importlib.metadata.version("paddleocr"),
            manifest_path=args.manifest,
            images_directory=args.images_dir,
            output_path=args.output,
            model_directory=args.model_dir,
            preprocessing_mode=args.preprocessing,
            threads=args.threads,
            warmup_count=args.warmup,
            initialization_ms=initialization_ms,
            recognize=recognize,
            sampler=sampler,
            process_started_at=PROCESS_ENTRY_AT,
            extra_metadata={
                "paddlepaddle_version": importlib.metadata.version("paddlepaddle"),
                "engine_backend": "paddle_static",
                "mkldnn": True,
                "model_source": "bos",
                "model_source_check_disabled": True,
                "model_present_before_process": model_present_before,
            },
        )
    except Exception as error:
        sampler.stop()
        print(f"PaddleOCR evaluation failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
