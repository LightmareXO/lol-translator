"""Sample a development video; no OCR-derived reference transcription."""

import argparse
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--times", type=float, nargs="+", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for timestamp in args.times:
        name = f"t{timestamp:08.3f}.png"
        path = args.output / name
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n",
                        "-ss", str(timestamp), "-i", str(args.video), "-frames:v", "1",
                        "-vf", "crop=1844:180:38:780", str(path)], check=True)
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        row = np.zeros((204, 1844, 3), np.uint8)
        row[24:] = image
        cv2.putText(row, f"{timestamp}s", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,255,255), 1)
        rows.append(row)
    for start in range(0, len(rows), 6):
        (args.output / f"sheet-{start//6:02}.png").write_bytes(
            cv2.imencode(".png", np.concatenate(rows[start:start+6]))[1].tobytes())
    (args.output / "times.json").write_text(json.dumps(args.times), encoding="utf-8")


if __name__ == "__main__":
    main()
