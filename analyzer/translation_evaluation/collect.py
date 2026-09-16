"""Export blind scoring packets and aggregate only explicitly reviewed judgements."""

import argparse
from collections import defaultdict
from pathlib import Path
import random
import statistics

try:
    from .evaluate import digest, read_json, save_json
except ImportError:
    from evaluate import digest, read_json, save_json


def percentile(values, p=.95):
    values = sorted(values)
    index = (len(values) - 1) * p
    left = int(index)
    return values[left] + (values[min(left+1, len(values)-1)] - values[left]) * (index-left)


def export(runs, output):
    rows, identities, warmups = [], {}, []
    for path in sorted(runs.glob("*/*.json")):
        result = read_json(path)
        if path.name.startswith("warmup-"):
            warmups.append({"experiment": path.parent.name, **result})
            continue
        if "job" not in result:
            continue
        body = {k: v for k, v in result.items() if k != "checksum"}
        if result.get("checksum") != digest(body):
            raise ValueError("corrupt result")
        identity = result["identity"]
        experiment = digest(identity)
        identities[experiment] = identity
        response = result.get("response") or {}
        message = response.get("message", {})
        rows.append({"result_id": digest([experiment, result["job"]]), "experiment": experiment,
                     "model": identity["model"], **result["job"],
                     "status": result["status"], "output": message.get("content", ""),
                     "thinking": message.get("thinking", ""), "wall_ms": result["wall_ms"],
                     "response": response, "errors": result["errors"],
                     "resources": result["resources"], "ps": result["ps"]})
    if len({r['result_id'] for r in rows}) != len(rows):
        raise ValueError("duplicate result")
    save_json(output / "identities.json", identities)
    save_json(output / "outputs.json", rows)
    save_json(output / "warmups.json", warmups)
    # Model/condition/result IDs are absent from the packet; map is separate.
    shuffled = sorted(rows, key=lambda r: r["result_id"])
    random.Random(93271).shuffle(shuffled)
    mapping, packet = {}, []
    for row in shuffled:
        blind = "review-" + digest(["blind-v1", row["result_id"]])[:16]
        mapping[blind] = row["result_id"]
        packet.append({"blind_id": blind, "sample_id": row["sample_id"],
                       "output": row["output"], "primary": row["primary"],
                       "review_status": "unreviewed", "severity": "undetermined", "error_types": [],
                       "rationale": "", "reviewer": "", "evidence": [], "semantic_signature": ""})
    save_json(output / "blind_mapping.json", mapping)
    # Never overwrite a file someone may have scored.
    scoring_path = output / "scoring.json"
    existing = read_json(scoring_path) if scoring_path.exists() else []
    known = {item["blind_id"] for item in existing}
    if known - mapping.keys():
        raise ValueError("export would orphan existing reviews")
    save_json(scoring_path, existing + [item for item in packet if item["blind_id"] not in known])
    save_json(output / "blind_packet.json", packet)
    return rows


def summarise(rows, scoring, mapping):
    by_id = {r["result_id"]: r for r in rows}
    reviews = {}
    for review in scoring:
        if review["blind_id"] not in mapping:
            raise ValueError("unknown blind ID")
        result_id = mapping[review["blind_id"]]
        if result_id not in by_id or result_id in reviews:
            raise ValueError("unknown or duplicate scored result")
        row = by_id[result_id]
        if review["sample_id"] != row["sample_id"] or review["output"] != row["output"]:
            raise ValueError("scoring/result correspondence mismatch")
        if review["review_status"] not in {"unreviewed", "ai_provisional", "human_confirmed"}:
            raise ValueError("invalid review status")
        if review["severity"] not in {"none", "minor", "terminology", "major", "undetermined"}:
            raise ValueError("invalid severity")
        if review["review_status"] != "unreviewed" and (not review["rationale"] or not review["reviewer"]):
            raise ValueError("reviewed judgement requires rationale and reviewer")
        if review["review_status"] == "human_confirmed" and not review.get("evidence"):
            raise ValueError("human confirmation requires evidence")
        allowed_errors = {"terminology", "omission", "hallucination", "format", "negation", "number", "actor", "sequence"}
        if not isinstance(review["error_types"], list) or set(review["error_types"]) - allowed_errors:
            raise ValueError("invalid error types")
        reviews[result_id] = review
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row["experiment"], row["split"], row["condition"], row["primary"])].append(row)
    results = []
    for (experiment, split, condition, primary), items in sorted(buckets.items()):
        ok = [r for r in items if r["status"] == "ok"]
        times = [r["wall_ms"] for r in ok]
        item = {"experiment": experiment, "model": items[0]["model"], "split": split,
                "condition": condition, "primary": primary, "requests": len(items),
                "subtitle_groups": len({r["group_id"] for r in items}),
                "request_failures": len(items)-len(ok), "wall_ms_median": statistics.median(times) if times else None,
                "wall_ms_p95": percentile(times) if times else None,
                "generated_tokens": sum(r["response"].get("eval_count", 0) for r in ok),
                "all_generated_tokens_including_failures": sum(r["response"].get("eval_count", 0) for r in items),
                "all_wall_ms_median": statistics.median([r["wall_ms"] for r in items]),
                "truncated_outputs": sum(r["response"].get("done_reason") == "length" for r in items),
                "thinking_outputs": sum(bool(r["thinking"]) for r in items)}
        for status in ("human_confirmed", "ai_provisional"):
            judged = [(r, reviews[r["result_id"]]) for r in items if
                      r["result_id"] in reviews and reviews[r["result_id"]]["review_status"] == status]
            determined = [(r, rev) for r, rev in judged if rev["severity"] != "undetermined"]
            determined_ids = {r["result_id"] for r, _ in determined}
            complete_groups = {r["group_id"] for r in items if all(
                other["result_id"] in determined_ids for other in items if other["group_id"] == r["group_id"])}
            item[status] = {
                "reviewed_units": len(judged), "determined_units": len(determined),
                "undetermined_units": len(judged) - len(determined),
                "fully_determined_groups": len(complete_groups),
                "major_fully_determined_groups": len({r["group_id"] for r, rev in determined
                    if r["group_id"] in complete_groups and rev["severity"] == "major"}),
                "major_units": sum(rev["severity"] == "major" for r, rev in determined),
                "major_groups": len({r["group_id"] for r, rev in determined if rev["severity"] == "major"}),
                "error_types": {kind: sum(kind in rev["error_types"] for r, rev in judged)
                                for kind in ("terminology", "omission", "hallucination", "format", "negation", "number", "actor", "sequence")}}
        item["unreviewed_units"] = sum(r["result_id"] not in reviews or
            reviews[r["result_id"]]["review_status"] == "unreviewed" for r in items)
        results.append(item)
    comparisons = []
    paired = defaultdict(dict)
    for row in rows:
        if row["primary"]:
            paired[(row["experiment"], row["sample_id"])][row["condition"]] = row
    for (experiment, sample), conditions in paired.items():
        for left, right in (("A", "B"), ("C", "D"), ("B", "D")):
            if left not in conditions or right not in conditions:
                continue
            lrev = reviews.get(conditions[left]["result_id"])
            rrev = reviews.get(conditions[right]["result_id"])
            if not lrev or not rrev or lrev["review_status"] == "unreviewed" or rrev["review_status"] == "unreviewed":
                continue
            if "undetermined" in (lrev["severity"], rrev["severity"]):
                continue
            comparisons.append({"experiment": experiment, "sample_id": sample, "pair": left+right,
                "left": lrev["severity"], "right": rrev["severity"],
                "review_status": "human_confirmed" if lrev["review_status"] == rrev["review_status"] == "human_confirmed" else "ai_provisional"})
    adjacent = []
    frame_groups = defaultdict(list)
    for row in rows:
        frame_groups[(row["experiment"], row["condition"], row["group_id"], row["sample_id"].rsplit("_f", 1)[0])].append(row)
    for (experiment, condition, group, line), frames in frame_groups.items():
        if len(frames) < 2:
            continue
        reviewed = [(r, reviews.get(r["result_id"])) for r in frames]
        signatures = {rev["semantic_signature"] for r, rev in reviewed if rev and
                      rev["review_status"] != "unreviewed" and rev.get("semantic_signature")}
        adjacent.append({"experiment": experiment, "condition": condition, "group_id": group,
                         "line": line, "frames": len(frames), "distinct_reviewed_meanings": len(signatures),
                         "meaning_changed": len(signatures) > 1,
                         "fully_reviewed": all(rev and rev["review_status"] != "unreviewed" and rev.get("semantic_signature") for r, rev in reviewed)})
    return {"conditions": results, "paired_judgements": comparisons, "adjacent_judgements": adjacent,
            "note": "Adjacent frames are auxiliary. AI judgements are provisional. No general error rate is inferred."}


def report_markdown(summary):
    lines = ["# 翻訳比較の集計", "", "主評価のみを表示します。隣接フレームは分母へ追加しません。",
             "人による確認とAI暫定採点は別集計です。判定不能と未採点を正解に含めません。", "",
             "| モデル／実験 | 条件 | 字幕数 | 人が確定した重大誤訳／確認済み | AI暫定の重大誤訳／判定済み | 未採点の出力数 | 上限到達 | 成功応答の中央値／p95 (ms) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in summary["conditions"]:
        if not item["primary"]:
            continue
        human, provisional = item["human_confirmed"], item["ai_provisional"]
        def ratio(data):
            denominator = data["fully_determined_groups"]
            return f'{data["major_fully_determined_groups"]}/{denominator}' if denominator else "判定済みなし"
        latency = "未測定" if item["wall_ms_median"] is None else f'{item["wall_ms_median"]:.1f} / {item["wall_ms_p95"]:.1f}'
        lines.append(f'| {item["model"]} / {item["experiment"][:10]} | {item["condition"]} | {item["subtitle_groups"]} | '
                     f'{ratio(human)} | {ratio(provisional)} | {item["unreviewed_units"]} | {item["truncated_outputs"]} | {latency} |')
    lines.extend(["", "APIが応答しても、訳文が空、途中で切れる、辞書や思考文を出力するといった失敗はありえます。",
                  "上限到達件数、出力形式の検査結果、意味の重大度を合わせて判断してください。",
                  "形式検査だけ済んだ出力は、意味について判定済みの分母に含めません。",
                  "誤りの種類、判定不能件数、対応条件の比較、隣接フレームの比較はsummary.jsonに保存しています。",
                  "この小標本の件数やp95を、一般的な誤訳率や性能へ外挿しません。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = export(args.runs, args.output)
    summary = summarise(rows, read_json(args.output / "scoring.json"), read_json(args.output / "blind_mapping.json"))
    save_json(args.output / "summary.json", summary)
    (args.output / "report.md").write_text(report_markdown(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
