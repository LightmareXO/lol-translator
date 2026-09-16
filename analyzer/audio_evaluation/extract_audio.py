"""Extract padded, mono 16 kHz PCM clips from the source videos."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from analyzer.audio_evaluation.core import load_json, save_json, sha256_file, validate_manifest


def probe_duration(path: Path) -> float:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", "--", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return float(json.loads(completed.stdout)["format"]["duration"])


def extract(manifest_path: Path, video_arguments: list[str], output_dir: Path) -> dict:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    videos: dict[str, Path] = {}
    for item in video_arguments:
        if "=" not in item:
            raise ValueError("--video must be VIDEO_ID=ABSOLUTE_PATH")
        video_id, raw_path = item.split("=", 1)
        path = Path(raw_path)
        if video_id in videos or video_id not in manifest["sources"]:
            raise ValueError(f"duplicate or unknown video id: {video_id}")
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"video is not an absolute file: {path}")
        source = manifest["sources"][video_id]
        if sha256_file(path) != source["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {video_id}")
        if abs(probe_duration(path) - float(source["duration_seconds"])) > 0.01:
            raise ValueError(f"duration mismatch for {video_id}")
        videos[video_id] = path
    required = {sample["video_id"] for sample in manifest["samples"]}
    if set(videos) != required:
        raise ValueError(f"video ids must be exactly {sorted(required)}")
    output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for sample in manifest["samples"]:
        clip_path = output_dir / f"{sample['id']}.wav"
        duration = sample["audio_end_seconds"] - sample["audio_start_seconds"]
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(sample["audio_start_seconds"]), "-i", str(videos[sample["video_id"]]),
                "-t", str(duration), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(clip_path),
            ],
            check=True,
        )
        rows.append(
            {
                "sample_id": sample["id"],
                "video_id": sample["video_id"],
                "audio_file": clip_path.name,
                "audio_sha256": sha256_file(clip_path),
                "audio_start_seconds": sample["audio_start_seconds"],
                "audio_end_seconds": sample["audio_end_seconds"],
                "duration_seconds": probe_duration(clip_path),
            }
        )
    extraction = {
        "manifest_sha256": sha256_file(manifest_path),
        "format": {"container": "wav", "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1},
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True, encoding="utf-8").splitlines()[0],
        "rows": rows,
    }
    save_json(output_dir / "extraction.json", extraction)
    return extraction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--video", action="append", required=True, help="VIDEO_ID=ABSOLUTE_PATH; repeat per source")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    extract(args.manifest, args.video, args.output_dir)


if __name__ == "__main__":
    main()
