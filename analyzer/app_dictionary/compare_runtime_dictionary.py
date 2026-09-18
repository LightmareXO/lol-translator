"""Compare the frozen v1 glossary and current app dictionary with one local model."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dictionary.dictionary import AppDictionary
from translation_evaluation.evaluate import (
    LocalClient,
    chat_with_retry,
    digest,
    glossary_text,
    make_payload,
    validate_thinking,
)


MODEL = "qwen3:4b-instruct-2507-q4_K_M"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def output_text(result: dict[str, Any]) -> str:
    if result["status"] != "ok":
        return ""
    return result["response"]["message"]["content"].strip()


def term_check(output: str, required: list[list[str]]) -> dict[str, Any]:
    groups = [
        {"alternatives": alternatives, "matched": [term for term in alternatives if term in output]}
        for alternatives in required
    ]
    return {"passed": all(group["matched"] for group in groups), "groups": groups}


def selection_check(case: dict[str, Any], target_ids: set[str]) -> dict[str, Any]:
    expected = set(case.get("expected_target_ids", []))
    forbidden = set(case.get("forbidden_target_ids", []))
    return {
        "passed": expected <= target_ids and not (forbidden & target_ids),
        "missing": sorted(expected - target_ids),
        "forbidden_selected": sorted(forbidden & target_ids),
    }


def condition_result(
    client: LocalClient,
    payload: dict[str, Any],
    reference: str,
    required_ja: list[list[str]],
) -> dict[str, Any]:
    result = chat_with_retry(client, payload, attempts=2)
    response = result.get("response") or {}
    output = output_text(result)
    return {
        "status": result["status"],
        "output": output,
        "reference": reference,
        "wall_ms": round(result["wall_ms"], 3),
        "done_reason": response.get("done_reason"),
        "prompt_eval_count": response.get("prompt_eval_count"),
        "eval_count": response.get("eval_count"),
        "errors": result["errors"],
        "automatic_term_check": term_check(output, required_ja),
    }


def compare(client: LocalClient, base: Path, dataset_path: Path) -> dict[str, Any]:
    dataset = read_json(dataset_path)
    prompts = read_json(base.parent / "translation_evaluation" / "prompts-instruct.json")
    legacy = read_json(base.parent / "translation_evaluation" / "glossary.json")
    dictionary = AppDictionary.load_default(base.parent)

    models = client.request("tags")["models"]
    model_info = next((item for item in models if item.get("name") == MODEL), None)
    if model_info is None:
        raise RuntimeError(f"local model is missing: {MODEL}")
    show = client.request("show", {"model": MODEL})
    if show.get("remote_host") or show.get("remote_model"):
        raise RuntimeError("remote Ollama model is forbidden")
    validate_thinking(show, prompts)

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(dataset["cases"], start=1):
        source = case["source_ko"]
        legacy_reference = glossary_text(legacy, source)
        selection = dictionary.select(source)
        target_ids = {
            target_id
            for match in selection.trace["matches"]
            for target_id in match["target_ids"]
        }
        legacy_payload = make_payload(
            MODEL,
            source,
            bool(legacy_reference),
            prompts,
            legacy,
            glossary_filter_text=source,
        )
        current_payload = make_payload(
            MODEL,
            source,
            bool(selection.prompt_text),
            prompts,
            {},
            terminology_reference=selection.prompt_text,
        )
        legacy_result = condition_result(
            client, legacy_payload, legacy_reference, case.get("required_ja", [])
        )
        current_result = condition_result(
            client, current_payload, selection.prompt_text, case.get("required_ja", [])
        )
        old_pass = legacy_result["automatic_term_check"]["passed"]
        new_pass = current_result["automatic_term_check"]["passed"]
        comparison = (
            "improved"
            if new_pass and not old_pass
            else "regressed"
            if old_pass and not new_pass
            else "unchanged_pass"
            if old_pass
            else "unchanged_fail"
        )
        rows.append(
            {
                **case,
                "selection_check": selection_check(case, target_ids),
                "current_dictionary_trace": selection.trace,
                "legacy_v1": legacy_result,
                "current": current_result,
                "comparison": comparison,
                "review_status": "automatic_term_check_only",
            }
        )
        print(f"{index}/{len(dataset['cases'])} {case['id']} {comparison}", flush=True)

    comparisons = Counter(row["comparison"] for row in rows)
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "holdout_used": dataset["holdout_used"],
            "dataset_name": dataset["name"],
            "dataset_sha256": digest(dataset),
            "assessment": "automatic terminology presence only; not a human translation-quality score",
        },
        "identity": {
            "model": MODEL,
            "digest": model_info.get("digest"),
            "size": model_info.get("size"),
            "details": model_info.get("details"),
            "ollama": client.request("version"),
            "prompt_version": prompts["version"],
            "options": prompts["options"],
            "think": prompts["qwen_think"],
            "legacy_dictionary_version": legacy["version"],
            "current_dictionary": dictionary.configuration(),
        },
        "summary": {
            "case_count": len(rows),
            "comparisons": dict(sorted(comparisons.items())),
            "selection_checks_passed": sum(row["selection_check"]["passed"] for row in rows),
            "legacy_term_checks_passed": sum(
                row["legacy_v1"]["automatic_term_check"]["passed"] for row in rows
            ),
            "current_term_checks_passed": sum(
                row["current"]["automatic_term_check"]["passed"] for row in rows
            ),
        },
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument("--dataset", type=Path, default=base / "comparison-dataset.json")
    parser.add_argument("--output", type=Path, default=base / "comparison-results.json")
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    result = compare(LocalClient(args.port, args.timeout), base, args.dataset)
    write_json(args.output, result)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
