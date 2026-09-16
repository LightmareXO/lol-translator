"""Extract fixed video/line bands and evaluate recognition against provisional references.

Line roles/counts are annotations, not automated detection. Whole two-line crops are
diagnostics only and are never silently concatenated into a spoken sentence.
"""

import argparse
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from analyzer.ocr_evaluation.core import sha256_file, edit_distance, remove_whitespace
from analyzer.translation_evaluation.evaluate import read_json, save_json, digest


def extract(video, specification, destination):
    import cv2
    import numpy as np
    destination.mkdir(parents=True, exist_ok=False)
    rows = []
    for sample in specification["samples"]:
        for index, offset in enumerate((-1, 0, 1), 1):
            timestamp = sample["time"] + offset / specification["fps"]
            frame = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp),
                "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                check=True, capture_output=True).stdout
            image = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.shape[:2] != (specification["height"], specification["width"]):
                raise ValueError("video dimensions differ from annotation")
            roles = ["annotation", "speech", "whole"] if "annotation" in sample else ["speech"]
            for role in roles:
                box = specification["crop_xywh" if role == "whole" else role + "_crop_xywh"]
                x, y, width, height = box
                if min(x, y) < 0 or min(width, height) <= 0 or x + width > image.shape[1] or y + height > image.shape[0]:
                    raise ValueError("invalid crop")
                identifier = f"{sample['id']}_{role}_f{index:02}"
                path = destination / (identifier + ".png")
                path.write_bytes(cv2.imencode(".png", image[y:y+height, x:x+width])[1].tobytes())
                rows.append({"id": identifier, "group_id": sample["id"], "video_id": specification["video_id"],
                    "timestamp_seconds": timestamp, "primary": index == 2, "split": specification["split"],
                    "line_role": role, "reading_order": {"annotation": 1, "speech": 2 if "annotation" in sample else 1, "whole": None}[role],
                    "color": {"annotation": "yellow", "speech": "white", "whole": "yellow_and_white"}[role],
                    "source_ko": sample[role] if role != "whole" else sample["annotation"] + "\n" + sample["speech"],
                    "reference_status": specification["reference_status"], "human_verified": False,
                    "tags": sample["tags"], "image_file": path.name, "image_sha256": sha256_file(path),
                    "crop_xywh": box, "diagnostic_only": role == "whole"})
    save_json(destination / "extraction.json", {"source_sha256": sha256_file(video),
        "specification": specification, "rows": rows, "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]})
    return rows


def metrics(reference, output):
    a, b = remove_whitespace(reference), remove_whitespace(output)
    return {"edits": edit_distance(reference, output), "characters": len(reference),
            "cer": edit_distance(reference, output)/len(reference),
            "whitespace_free_edits": edit_distance(a, b), "whitespace_free_characters": len(a),
            "whitespace_free_cer": edit_distance(a, b)/len(a), "exact": reference == output}


def recognise(directory, model_directory):
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(model_directory.resolve())
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["MKL_NUM_THREADS"] = "4"
    from paddleocr import TextRecognition
    from analyzer.ocr_evaluation.run_paddle import result_text_and_score
    from analyzer.ocr_evaluation.preprocessing import load_image, preprocess
    extraction = read_json(directory / "extraction.json")
    started = time.perf_counter()
    model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", device="cpu", engine="paddle_static",
                            enable_hpi=False, enable_mkldnn=True, cpu_threads=4)
    load_ms = (time.perf_counter()-started)*1000
    rows = []
    warmup_image = preprocess(load_image(directory / extraction["rows"][0]["image_file"]), "contrast")
    for _ in range(3):
        list(model.predict(input=warmup_image, batch_size=1))
    for row in extraction["rows"]:
        path = directory / row["image_file"]
        if sha256_file(path) != row["image_sha256"]:
            raise ValueError("image changed after extraction")
        image = load_image(path)
        started = time.perf_counter()
        predictions = list(model.predict(input=preprocess(image, "contrast"), batch_size=1))
        elapsed = (time.perf_counter()-started)*1000
        if len(predictions) != 1:
            raise ValueError("unexpected recognition count")
        raw, confidence = result_text_and_score(predictions[0])
        rows.append({**row, "ocr_ko": raw, "ocr_confidence": confidence, "ocr_ms": elapsed,
                     "metrics": metrics(row["source_ko"], raw)})
    result = {"extraction_sha256": digest(extraction), "source_sha256": extraction["source_sha256"],
        "model": "korean_PP-OCRv5_mobile_rec", "preprocessing": "contrast", "cpu_threads": 4,
        "paddleocr": importlib.metadata.version("paddleocr"), "paddlepaddle": importlib.metadata.version("paddlepaddle"),
        "initialization_ms": load_ms, "rows": rows}
    save_json(directory / "ocr-results.json", result)
    save_json(directory / "translation-dataset.json", [row for row in rows if not row["diagnostic_only"]])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--specification", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.video:
        extract(args.video, read_json(args.specification), args.output)
    if args.model_dir:
        if (args.output / "ocr-results.json").exists():
            raise ValueError("OCR results exist; use a separate experiment directory")
        recognise(args.output, args.model_dir)


if __name__ == "__main__":
    main()
