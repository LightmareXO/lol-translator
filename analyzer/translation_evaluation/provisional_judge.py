"""Blind, resumable provisional semantic grading with a separate local Ollama model."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random

try:
    from .evaluate import LocalClient, digest, read_json, save_json
except ImportError:
    from evaluate import LocalClient, digest, read_json, save_json


SYSTEM = """あなたは韓国語と日本語とLeague of Legendsに詳しい翻訳評価者です。
韓国語原文に対して、候補の日本語がゲーム内の判断をどの程度保持するかだけを判定してください。
入力の reference_ja と must_preserve は別のAIが作った暫定的な意味基準です。韓国語原文と合わせて利用してください。
reference_status が ambiguous の場合は、候補が明白に無関係でない限り undetermined にしてください。
候補の生成条件やモデル名は推測しないでください。
severity: none=意味を保持、minor=意味を壊さない軽微な不自然さ、terminology=重要でない用語誤り、
major=勝敗判断・行動・主体・否定・数値・重要用語を壊す誤り、undetermined=原文自体が曖昧で判定不能。
error_types は terminology, omission, hallucination, format, negation, number, actor, sequence のみ。
短い日本語の rationale と、意味を比較するための semantic_signature を必ず返してください。
同じ候補文は常に同じ判定にしてください。これは人手確認前の仮採点です。"""

SCHEMA = {"type": "object", "properties": {"judgements": {"type": "array", "items": {
    "type": "object", "properties": {
        "candidate_id": {"type": "string"},
        "severity": {"type": "string", "enum": ["none", "minor", "terminology", "major", "undetermined"]},
        "error_types": {"type": "array", "items": {"type": "string", "enum": [
            "terminology", "omission", "hallucination", "format", "negation", "number", "actor", "sequence"]}},
        "rationale": {"type": "string"}, "semantic_signature": {"type": "string"}},
    "required": ["candidate_id", "severity", "error_types", "rationale", "semantic_signature"]}}},
    "required": ["judgements"]}


def validate(response, expected):
    parsed = json.loads(response)
    rows = parsed.get("judgements")
    if not isinstance(rows, list) or {row.get("candidate_id") for row in rows} != set(expected):
        raise ValueError("judge candidate correspondence mismatch")
    for row in rows:
        if not row.get("rationale") or not row.get("semantic_signature"):
            raise ValueError("judge omitted rationale")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args()
    client = LocalClient(args.port, 180)
    models = client.request("tags")["models"]
    model = next((item for item in models if item["name"] == args.model), None)
    if model is None or ":cloud" in args.model:
        raise ValueError("judge model must already be installed locally")
    show = client.request("show", {"model": args.model})
    if show.get("remote_host") or show.get("remote_model"):
        raise ValueError("remote judge model is forbidden")

    outputs = [row for row in read_json(args.outputs) if row["primary"] and row["status"] == "ok"]
    references = {row["id"]: row["source_ko"] for row in read_json(args.dataset)}
    targets = {row["sample_id"]: row for row in read_json(args.targets)}
    groups = defaultdict(list)
    for row in outputs:
        groups[row["sample_id"]].append(row)
    identity = {"judge_model": args.model, "judge_digest": model["digest"], "model_info": show.get("model_info"),
                "system_sha256": digest(SYSTEM), "schema_sha256": digest(SCHEMA),
                "outputs_sha256": digest(outputs), "dataset_sha256": digest(references),
                "targets_sha256": digest(targets),
                "settings": {"temperature": 0, "seed": 93271, "num_predict": 1024, "think": False},
                "runner_schema": 2, "runner_sha256": digest(Path(__file__).read_text(encoding="utf-8"))}
    args.output.mkdir(parents=True, exist_ok=True)
    identity_path = args.output / "identity.json"
    if identity_path.exists() and read_json(identity_path) != identity:
        raise ValueError("judge identity changed; use a new output directory")
    save_json(identity_path, identity)

    decisions = []
    for index, (sample_id, rows) in enumerate(sorted(groups.items()), 1):
        source_values = {references[row["sample_id"]] for row in rows}
        if len(source_values) != 1:
            raise ValueError("group has inconsistent Korean references")
        unique = sorted({row["output"] for row in rows})
        group = rows[0]["group_id"]
        if sample_id not in targets:
            raise ValueError("missing provisional meaning target")
        candidates = [{"candidate_id": "candidate-" + digest([sample_id, output])[:12], "ja": output}
                      for output in unique]
        random.Random(digest([93271, sample_id])).shuffle(candidates)
        expected = {item["candidate_id"]: item["ja"] for item in candidates}
        target = targets[sample_id]
        job = {"sample_id": sample_id, "group_id": group, "source_ko": source_values.pop(),
               "reference_ja": target["reference_ja"], "must_preserve": target["must_preserve"],
               "reference_status": target["reference_status"], "candidates": candidates}
        job_digest = digest(job)
        result_path = args.output / f"{sample_id}-{job_digest[:16]}.json"
        if result_path.exists():
            result = read_json(result_path)
            if result.get("job_sha256") != job_digest or result.get("identity") != identity:
                raise ValueError("cached judge result mismatch")
            judgements = result["judgements"]
        else:
            payload = {"model": args.model, "stream": False, "think": False, "format": SCHEMA,
                       "options": {"temperature": 0, "seed": 93271, "num_predict": 1024},
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": json.dumps(job, ensure_ascii=False)}]}
            response = client.request("chat", payload)
            judgements = validate(response["message"]["content"], expected)
            result = {"job_sha256": job_digest, "identity": identity, "job": job,
                      "judgements": judgements, "response_metadata": {
                          key: response.get(key) for key in ("done_reason", "total_duration", "load_duration",
                                                            "prompt_eval_count", "eval_count")}}
            save_json(result_path, result)
        by_candidate = {row["candidate_id"]: row for row in judgements}
        for candidate_id, output in expected.items():
            judgement = by_candidate[candidate_id]
            decisions.append({"sample_id": sample_id, "group_id": group, "output": output,
                "severity": judgement["severity"],
                "error_types": judgement["error_types"], "rationale": judgement["rationale"],
                "semantic_signature": judgement["semantic_signature"], "review_status": "ai_provisional",
                "reviewer": f"{args.model} local blind semantic judge",
                "evidence": [f"dataset:{group}", f"judge-result:{result_path.name}"]})
        save_json(args.output / "decisions.json", decisions)
        print(f"{index}/{len(groups)} {sample_id}")


if __name__ == "__main__":
    main()
