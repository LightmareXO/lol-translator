"""Compare two dictionary conditions with the same local translation model."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dictionary.dictionary import AppDictionary
from app_dictionary.hashing import HASH_ALGORITHM, canonical_json_sha256
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


def selection_target_ids(selection: Any) -> set[str]:
    return {
        target_id
        for match in selection.trace["matches"]
        for target_id in match["target_ids"]
    }


def load_dictionary_from_git_ref(repo_root: Path, ref: str) -> AppDictionary:
    dictionary_path = "analyzer/app_dictionary"
    manifest = json.loads(
        subprocess.run(
            ["git", "show", f"{ref}:{dictionary_path}/manifest.json"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    )
    files = [
        manifest["official_file"]["path"],
        manifest["sources_file"]["path"],
        *(item["path"] for item in manifest["alias_files"]),
        *(item["path"] for item in manifest["term_files"]),
    ]
    temporary = tempfile.TemporaryDirectory()
    base = Path(temporary.name) / "app_dictionary"
    base.mkdir()
    for relative in files:
        output = subprocess.run(
            ["git", "show", f"{ref}:{dictionary_path}/{relative}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        ).stdout
        if sys.platform == "win32":
            output = output.replace(b"\n", b"\r\n")
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output)
    manifest["schema_version"] = 3
    manifest["hash_algorithm"] = HASH_ALGORITHM
    for item in [
        manifest["official_file"],
        manifest["sources_file"],
        *manifest["alias_files"],
        *manifest["term_files"],
    ]:
        item["sha256"] = canonical_json_sha256(
            json.loads((base / item["path"]).read_text(encoding="utf-8"))
        )
    (base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    dictionary = AppDictionary(base / "manifest.json")
    temporary.cleanup()
    return dictionary


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
        target_ids = selection_target_ids(selection)
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


def compare_revisions(
    client: LocalClient,
    base: Path,
    dataset_path: Path,
    baseline_ref: str,
) -> dict[str, Any]:
    dataset = read_json(dataset_path)
    prompts = read_json(base.parent / "translation_evaluation" / "prompts-instruct.json")
    repo_root = base.parents[1]
    baseline = load_dictionary_from_git_ref(repo_root, baseline_ref)
    current = AppDictionary.load_default(base.parent)

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
        baseline_selection = baseline.select(source)
        current_selection = current.select(source)
        baseline_targets = selection_target_ids(baseline_selection)
        current_targets = selection_target_ids(current_selection)
        baseline_payload = make_payload(
            MODEL,
            source,
            bool(baseline_selection.prompt_text),
            prompts,
            {},
            terminology_reference=baseline_selection.prompt_text,
        )
        current_payload = make_payload(
            MODEL,
            source,
            bool(current_selection.prompt_text),
            prompts,
            {},
            terminology_reference=current_selection.prompt_text,
        )
        baseline_result = condition_result(
            client,
            baseline_payload,
            baseline_selection.prompt_text,
            case.get("required_ja", []),
        )
        current_result = condition_result(
            client,
            current_payload,
            current_selection.prompt_text,
            case.get("required_ja", []),
        )
        old_pass = baseline_result["automatic_term_check"]["passed"]
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
        expected_baseline = set(case.get("expected_baseline_target_ids", []))
        forbidden_baseline = set(case.get("forbidden_baseline_target_ids", []))
        baseline_selection_check = {
            "passed": expected_baseline <= baseline_targets
            and not (forbidden_baseline & baseline_targets),
            "missing": sorted(expected_baseline - baseline_targets),
            "forbidden_selected": sorted(forbidden_baseline & baseline_targets),
        }
        rows.append(
            {
                **case,
                "baseline_selection_check": baseline_selection_check,
                "current_selection_check": selection_check(case, current_targets),
                "baseline_dictionary_trace": baseline_selection.trace,
                "current_dictionary_trace": current_selection.trace,
                "baseline": baseline_result,
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
            "baseline_ref": baseline_ref,
            "baseline_dictionary": baseline.configuration(),
            "current_dictionary": current.configuration(),
        },
        "summary": {
            "case_count": len(rows),
            "comparisons": dict(sorted(comparisons.items())),
            "baseline_selection_checks_passed": sum(
                row["baseline_selection_check"]["passed"] for row in rows
            ),
            "current_selection_checks_passed": sum(
                row["current_selection_check"]["passed"] for row in rows
            ),
            "baseline_term_checks_passed": sum(
                row["baseline"]["automatic_term_check"]["passed"] for row in rows
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
    parser.add_argument(
        "--baseline-ref",
        help="Compare the dictionary at this Git ref with the current dictionary",
    )
    args = parser.parse_args()
    client = LocalClient(args.port, args.timeout)
    result = (
        compare_revisions(client, base, args.dataset, args.baseline_ref)
        if args.baseline_ref
        else compare(client, base, args.dataset)
    )
    write_json(args.output, result)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
