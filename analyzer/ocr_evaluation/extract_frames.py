"""Extract manifest frames and crop the normalized subtitle region with FFmpeg."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.ocr_evaluation.core import (  # type: ignore
        load_manifest,
        normalized_region_to_pixels,
        sha256_file,
    )
else:
    from .core import load_manifest, normalized_region_to_pixels, sha256_file


def probe_video(ffprobe: str, video: Path) -> dict[str, object]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-of",
        "json",
        str(video),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    streams = json.loads(completed.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError("expected exactly one primary video stream")
    return streams[0]


def extract(
    manifest_path: Path,
    video: Path,
    output_directory: Path,
    ffmpeg: str,
    ffprobe: str,
    force: bool,
) -> None:
    manifest = load_manifest(manifest_path)
    if not video.is_absolute() or not video.is_file():
        raise ValueError("--video must be an existing absolute file")
    expected_hash = manifest["source"]["sha256"]
    actual_hash = sha256_file(video)
    if actual_hash != expected_hash:
        raise ValueError(f"video SHA-256 mismatch: expected {expected_hash}, got {actual_hash}")

    probe = probe_video(ffprobe, video)
    source = manifest["source"]
    if (probe.get("width"), probe.get("height")) != (source["width"], source["height"]):
        raise ValueError(
            "video dimensions do not match manifest: "
            f"expected {source['width']}x{source['height']}, "
            f"got {probe.get('width')}x{probe.get('height')}"
        )
    left, top, width, height = normalized_region_to_pixels(
        source["subtitle_region"], source["width"], source["height"]
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    for image in manifest["images"]:
        output_path = output_directory / image["image_file"]
        if output_path.exists() and not force:
            raise FileExistsError(f"refusing to overwrite {output_path}; pass --force")
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if force else "-n",
            "-ss",
            f"{image['timestamp_seconds']:.6f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"crop={width}:{height}:{left}:{top}",
            str(output_path),
        ]
        subprocess.run(command, check=True)

    provenance = {
        "manifest": str(manifest_path.resolve()),
        "video": str(video),
        "video_sha256": actual_hash,
        "ffmpeg": shutil.which(ffmpeg) or ffmpeg,
        "ffprobe": shutil.which(ffprobe) or ffprobe,
        "probed_stream": probe,
        "pixel_crop": {"x": left, "y": top, "width": width, "height": height},
        "image_count": len(manifest["images"]),
    }
    (output_directory / "extraction.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        extract(
            args.manifest,
            args.video,
            args.output_dir,
            args.ffmpeg,
            args.ffprobe,
            args.force,
        )
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"Frame extraction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

