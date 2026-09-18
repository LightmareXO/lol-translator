"""Flag obvious non-subtitle outputs; heuristics do not establish semantic correctness."""

import argparse
from pathlib import Path

try:
    from .evaluate import read_json, save_json
except ImportError:
    from evaluate import read_json, save_json


def check(rows):
    for row in rows:
        if row["review_status"] != "unreviewed":
            continue
        text = row["output"]
        reason = None
        if not text.strip():
            reason = "訳文が空。意味の正誤は判定不能。APIのエラー記録と併せて確認する。"
        elif text.startswith("Okay, ") and "translate" in text[:500].lower():
            reason = "英語の翻訳手順説明が本文に混入。形式検査のみ実施し、意味の正誤は採点しない。"
        elif text.startswith("リーグ・オブ・レジェンドの用語集"):
            reason = "用語集を訳文へ出力。形式検査のみ実施し、末尾の字幕部分を含む意味の採点は未実施。"
        if reason:
            row.update({"review_status": "ai_provisional", "severity": "undetermined", "error_types": ["format"],
                        "rationale": reason, "reviewer": "format_checks.py heuristic; not semantic grading",
                        "evidence": ["exact saved output"], "semantic_signature": ""})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scoring", type=Path, required=True)
    args = parser.parse_args()
    save_json(args.scoring, check(read_json(args.scoring)))


if __name__ == "__main__":
    main()
