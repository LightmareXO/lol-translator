"""Join manifest, raw ASR, and separate review annotations into handoff tables."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from analyzer.audio_evaluation.core import load_json, save_json, sha256_file, validate_manifest


CATEGORIES = {"near_verbatim", "paraphrase_or_summary", "unspoken_supplement"}
STATUSES = {"confirmed_by_listening", "unconfirmed_no_audio_input"}


def build(manifest_path: Path, transcripts_path: Path, reviews_path: Path, output_dir: Path) -> dict:
    manifest = load_json(manifest_path)
    transcripts = load_json(transcripts_path)
    reviews_document = load_json(reviews_path)
    validate_manifest(manifest)
    if transcripts["manifest_sha256"] != sha256_file(manifest_path):
        raise ValueError("transcript manifest hash mismatch")
    transcript_rows = {row["sample_id"]: row for row in transcripts["rows"]}
    reviews = {review["subtitle_id"]: review for review in reviews_document["reviews"]}
    expected_lines = {line["subtitle_id"] for sample in manifest["samples"] for line in sample["lines"]}
    if set(reviews) != expected_lines:
        raise ValueError("review subtitle ids differ from manifest")
    if set(transcript_rows) != {sample["id"] for sample in manifest["samples"]}:
        raise ValueError("transcript sample ids differ from manifest")

    rows = []
    confirmed_counts = {category: 0 for category in sorted(CATEGORIES)}
    candidate_counts = {category: 0 for category in sorted(CATEGORIES)}
    for sample in manifest["samples"]:
        transcript = transcript_rows[sample["id"]]
        segment_text = " | ".join(
            f"{segment['source_start_seconds']:.3f}-{segment['source_end_seconds']:.3f} {segment['text'].strip()}"
            for segment in transcript["segments"]
        )
        for line in sample["lines"]:
            review = reviews[line["subtitle_id"]]
            if review["confirmation_status"] not in STATUSES:
                raise ValueError(f"unknown confirmation status for {line['subtitle_id']}")
            classification = review["classification"]
            candidate = review.get("candidate_category")
            if classification != "unresolved" and classification not in CATEGORIES:
                raise ValueError(f"unknown classification for {line['subtitle_id']}")
            if candidate is not None and candidate not in CATEGORIES:
                raise ValueError(f"unknown candidate category for {line['subtitle_id']}")
            if classification in CATEGORIES and review["confirmation_status"] == "confirmed_by_listening":
                confirmed_counts[classification] += 1
            if candidate in CATEGORIES:
                candidate_counts[candidate] += 1
            rows.append(
                {
                    "video_id": sample["video_id"],
                    "sample_id": sample["id"],
                    "subtitle_id": line["subtitle_id"],
                    "line_role": line["role"],
                    "color": line["color"],
                    "subtitle_ko": line["text_ko"],
                    "reference_status": line["reference_status"],
                    "display_start_seconds": line.get("display_start_seconds", sample["display_start_seconds"]),
                    "display_end_seconds": line.get("display_end_seconds", sample["display_end_seconds"]),
                    "audio_start_seconds": sample["audio_start_seconds"],
                    "audio_end_seconds": sample["audio_end_seconds"],
                    "asr_raw_text": transcript["raw_text"],
                    "asr_source_timed_segments": segment_text,
                    "classification": classification,
                    "candidate_category": candidate or "",
                    "discrepancy": review["discrepancy"],
                    "confirmation_status": review["confirmation_status"],
                    "evidence": review["evidence"],
                }
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with (output_dir / "correspondence.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "manifest_sha256": sha256_file(manifest_path),
        "transcripts_sha256": sha256_file(transcripts_path),
        "reviews_sha256": sha256_file(reviews_path),
        "line_count": len(rows),
        "confirmed_counts": confirmed_counts,
        "unconfirmed_candidate_counts": candidate_counts,
        "unresolved_lines": sum(row["classification"] == "unresolved" for row in rows),
        "rows": rows,
    }
    save_json(output_dir / "correspondence.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--transcripts", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.manifest, args.transcripts, args.reviews, args.output_dir)


if __name__ == "__main__":
    main()
