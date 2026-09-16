"""Two preregistered, ground-truth-independent transforms of existing crops."""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def transform(image, candidate):
    if candidate == "vertical_band":
        # Original 96px band: preserve all glyphs and their black shadow.
        return image[10:80, :].copy()
    if candidate == "white_mask":
        high = image.max(axis=2).astype(np.int16)
        low = image.min(axis=2).astype(np.int16)
        mask = ((low >= 170) & (high - low <= 60)).astype(np.uint8) * 255
        return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    raise ValueError(candidate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    provenance = {"rules": {"vertical_band": "rows [10,80), all columns",
                            "white_mask": "min(BGR)>=170 and max-min<=60; white on black"},
                  "files": {}}
    for candidate in provenance["rules"]:
        directory = args.output / candidate
        directory.mkdir(exist_ok=True)
        sheet = []
        for path in sorted(args.images.glob("*.png")):
            data = path.read_bytes()
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            output = transform(image, candidate)
            target = directory / path.name
            if target.exists():
                raise FileExistsError(target)
            target.write_bytes(cv2.imencode(".png", output)[1].tobytes())
            provenance["files"][f"{candidate}/{path.name}"] = {
                "input_sha256": hashlib.sha256(data).hexdigest(),
                "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            if path.stem.endswith("f02"):
                row = np.zeros((120, image.shape[1], 3), dtype=np.uint8)
                row[24:24 + output.shape[0]] = output
                cv2.putText(row, path.stem, (10, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (255,255,255), 1)
                sheet.append(row)
        (args.output / f"{candidate}-sheet.png").write_bytes(
            cv2.imencode(".png", np.concatenate(sheet))[1].tobytes())
    (args.output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
