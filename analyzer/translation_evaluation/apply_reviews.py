"""Apply explicit, model-blind output judgements without overwriting human reviews."""

import argparse
from pathlib import Path

try:
    from .evaluate import read_json, save_json
except ImportError:
    from evaluate import read_json, save_json


def apply(packet, decisions):
    exact = {(r.get("sample_id"), r["output"]): r for r in decisions if r.get("sample_id")}
    grouped = {(r["group_id"], r["output"]): r for r in decisions if not r.get("sample_id")}
    if len(exact) + len(grouped) != len(decisions):
        raise ValueError("duplicate decision key")
    count = 0
    for row in packet:
        group = row["sample_id"].split("_")[0]
        decision = exact.get((row["sample_id"], row["output"])) or grouped.get((group, row["output"]))
        if decision and row["review_status"] != "human_confirmed":
            row.update({key: value for key, value in decision.items()
                        if key not in {"group_id", "sample_id", "output"}})
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scoring", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    args = parser.parse_args()
    rows = read_json(args.scoring)
    count = apply(rows, read_json(args.decisions))
    save_json(args.scoring, rows)
    print(f"Applied explicit judgements to {count} output units")


if __name__ == "__main__":
    main()
