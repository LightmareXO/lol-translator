"""Run EasyOCR Korean recognition in an isolated CPU process."""

from __future__ import annotations

import argparse
import importlib.metadata
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


MODEL_ID = "korean_g2"


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
    sampler = MemorySampler()
    sampler.start()
    try:
        import easyocr
        import torch

        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        initialization_started = time.perf_counter()
        reader = easyocr.Reader(
            ["ko", "en"],
            gpu=False,
            model_storage_directory=str(args.model_dir),
            detector=False,
            recognizer=True,
            download_enabled=True,
            quantize=True,
            verbose=False,
        )
        initialization_ms = (time.perf_counter() - initialization_started) * 1000

        def recognize(image: Any) -> tuple[str, float | None]:
            results = reader.recognize(
                image,
                horizontal_list=None,
                free_list=None,
                decoder="greedy",
                batch_size=1,
                workers=0,
                detail=1,
                paragraph=False,
            )
            if len(results) != 1:
                raise ValueError(f"expected one EasyOCR result, got {len(results)}")
            _, text, confidence = results[0]
            return str(text), float(confidence)

        run_evaluation(
            engine="easyocr",
            model_id=MODEL_ID,
            engine_version=importlib.metadata.version("easyocr"),
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
                "torch_version": torch.__version__,
                "decoder": "greedy",
                "quantized": True,
                "detector_loaded": False,
                "model_present_before_process": model_present_before,
            },
        )
    except Exception as error:
        sampler.stop()
        print(f"EasyOCR evaluation failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
