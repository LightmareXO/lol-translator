"""Aggregate line OCR separately from full two-line recognition."""

import argparse
from collections import defaultdict
from pathlib import Path
import statistics

try:
    from .evaluate import read_json, save_json
    from .video_dataset import metrics
except ImportError:
    from evaluate import read_json, save_json
    from video_dataset import metrics


def aggregate(rows):
    characters = sum(r["metrics"]["characters"] for r in rows)
    clean_characters = sum(r["metrics"]["whitespace_free_characters"] for r in rows)
    return {"groups": len({r["group_id"] for r in rows}), "units": len(rows),
        "cer": sum(r["metrics"]["edits"] for r in rows)/characters if characters else None,
        "whitespace_free_cer": sum(r["metrics"]["whitespace_free_edits"] for r in rows)/clean_characters if clean_characters else None,
        "exact_units": sum(r["metrics"]["exact"] for r in rows),
        "whitespace_free_exact_units": sum(r["metrics"]["whitespace_free_edits"] == 0 for r in rows),
        "median_ocr_ms": statistics.median([r["ocr_ms"] for r in rows]) if rows else None}


def summarise(result):
    rows = result["rows"]
    primary = [r for r in rows if r["primary"] and not r["diagnostic_only"]]
    by_group = defaultdict(list)
    for row in primary:
        by_group[row["group_id"]].append(row)
    combined = []
    for group, lines in by_group.items():
        if len(lines) < 2:
            continue
        lines.sort(key=lambda r: r["reading_order"])
        # This concatenation is metric-only, never a translation input.
        reference = "\n".join(r["source_ko"] for r in lines)
        predicted = "\n".join(r["ocr_ko"] for r in lines)
        combined.append({"group_id": group, "line_ids": [r["id"] for r in lines],
            "metrics_for_separate_line_outputs": metrics(reference, predicted),
            "whole_crop": next(r for r in rows if r["group_id"] == group and r["primary"] and r["diagnostic_only"])})
    return {"primary_lines": aggregate(primary),
        "auxiliary_lines": aggregate([r for r in rows if not r["primary"] and not r["diagnostic_only"]]),
        "whole_two_line_diagnostics": combined,
        "note": "AI provisional transcriptions; line roles are visually annotated, not automatically detected. Adjacent frames are not independent subtitles."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = read_json(args.input)
    summary = summarise(data)
    save_json(args.output / "ocr-results.json", data)
    save_json(args.output / "summary.json", summary)
    text = ["# 追加動画のOCR確認", "", "転記と役割はAIによる暫定確認です。", "人による確認済みの認識精度ではありません。", "",
        "| 字幕ID | 正解候補 | OCR生出力 | CER | 空白除外CER |", "| --- | --- | --- | --- | --- |"]
    for row in data["rows"]:
        if not row["primary"]:
            continue
        cells = [row["id"], row["source_ko"], row["ocr_ko"], f'{row["metrics"]["cer"]:.4f}', f'{row["metrics"]["whitespace_free_cer"]:.4f}']
        text.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " / ") for c in cells) + " |")
    (args.output / "comparison.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(summary["primary_lines"])


if __name__ == "__main__":
    main()
