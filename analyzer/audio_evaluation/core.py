"""Shared validation and timestamp helpers for the audio evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    sources = manifest.get("sources")
    samples = manifest.get("samples")
    if not isinstance(sources, dict) or not isinstance(samples, list) or not samples:
        raise ValueError("manifest needs sources and samples")
    seen_samples: set[str] = set()
    seen_lines: set[str] = set()
    padding = float(manifest["audio_padding_seconds"])
    for sample in samples:
        sample_id = sample["id"]
        if not isinstance(sample_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sample_id):
            raise ValueError(f"unsafe sample id: {sample_id}")
        if sample_id in seen_samples:
            raise ValueError(f"duplicate sample id: {sample_id}")
        seen_samples.add(sample_id)
        if sample["video_id"] not in sources:
            raise ValueError(f"unknown video id: {sample['video_id']}")
        display_start = float(sample["display_start_seconds"])
        display_end = float(sample["display_end_seconds"])
        audio_start = float(sample["audio_start_seconds"])
        audio_end = float(sample["audio_end_seconds"])
        if not 0 <= audio_start < display_start < display_end < audio_end:
            raise ValueError(f"invalid intervals for {sample_id}")
        if audio_start > display_start - padding or audio_end < display_end + padding:
            raise ValueError(f"insufficient audio padding for {sample_id}")
        if audio_end > float(sources[sample["video_id"]]["duration_seconds"]):
            raise ValueError(f"audio interval exceeds source for {sample_id}")
        if not sample.get("lines"):
            raise ValueError(f"sample has no lines: {sample_id}")
        for line in sample["lines"]:
            subtitle_id = line["subtitle_id"]
            if not isinstance(subtitle_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", subtitle_id):
                raise ValueError(f"unsafe subtitle id: {subtitle_id}")
            if subtitle_id in seen_lines:
                raise ValueError(f"duplicate subtitle id: {subtitle_id}")
            seen_lines.add(subtitle_id)
            line_start = float(line.get("display_start_seconds", display_start))
            line_end = float(line.get("display_end_seconds", display_end))
            if line_start < display_start or line_end > display_end or line_start >= line_end:
                raise ValueError(f"line interval outside sample: {subtitle_id}")


def source_time(clip_start_seconds: float, clip_time_seconds: float) -> float:
    """Convert clip-relative ASR time to source-video time."""
    return round(float(clip_start_seconds) + float(clip_time_seconds), 3)


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
