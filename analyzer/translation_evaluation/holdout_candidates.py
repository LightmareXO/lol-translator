"""Extract a fixed-interval visual survey without using OCR to select samples."""

import argparse
from pathlib import Path
import subprocess

try:
    from .evaluate import save_json
except ImportError:
    from evaluate import save_json


def candidate_timestamp(start, interval, index):
    """Return the source time represented by an fps-filter output frame."""
    return start + interval / 2 + index * interval


def main():
    import cv2
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=3)
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args()
    if args.start < 0 or args.interval <= 0 or args.output.exists():
        raise ValueError("invalid range or output already exists")
    frames = args.output / "frames"
    frames.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(args.start),
                    "-i", str(args.video), "-vf", f"fps=1/{args.interval},crop=1844:180:38:780",
                    "-start_number", "0", str(frames / "%04d.png")], check=True)
    paths = sorted(frames.glob("*.png"))
    samples = []
    for index, path in enumerate(paths):
        timestamp = candidate_timestamp(args.start, args.interval, index)
        samples.append({"candidate_id": f"h{index:04}", "timestamp_seconds": timestamp,
                        "image_file": path.name, "selection_status": "unreviewed"})
    sheets = args.output / "sheets"
    sheets.mkdir()
    for start in range(0, len(samples), 10):
        rows = []
        for sample in samples[start:start + 10]:
            image = cv2.imdecode(np.frombuffer((frames / sample["image_file"]).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            canvas = np.zeros((204, 1844, 3), np.uint8)
            canvas[24:] = image
            cv2.putText(canvas, f'{sample["candidate_id"]}  {sample["timestamp_seconds"]:.1f}s',
                        (10, 18), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
            rows.append(canvas)
        (sheets / f"sheet-{start // 10:02}.png").write_bytes(cv2.imencode(".jpg", np.concatenate(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes())
    save_json(args.output / "candidates.json", {"selection_rule": "3-second fixed interval centered by ffmpeg fps filter; visual review; no OCR-success filtering",
              "start_seconds": args.start, "interval_seconds": args.interval, "samples": samples})


if __name__ == "__main__":
    main()
