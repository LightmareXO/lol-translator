"""Validate the app dictionary and optionally compare an older official generation."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dictionary.dictionary import AppDictionary, collision_report


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    before = {entry["id"]: entry for entry in previous["entries"]}
    after = {entry["id"]: entry for entry in current["entries"]}
    shared = before.keys() & after.keys()
    return {
        "from": previous["dictionary_version"],
        "to": current["dictionary_version"],
        "added_ids": sorted(after.keys() - before.keys()),
        "removed_ids": sorted(before.keys() - after.keys()),
        "renamed": [
            {
                "id": identifier,
                "before": {"ko": before[identifier]["ko"], "ja": before[identifier]["ja"]},
                "after": {"ko": after[identifier]["ko"], "ja": after[identifier]["ja"]},
            }
            for identifier in sorted(shared)
            if (before[identifier]["ko"], before[identifier]["ja"])
            != (after[identifier]["ko"], after[identifier]["ja"])
        ],
    }


def validate(base: Path, compare: Path | None = None) -> dict[str, Any]:
    manifest = read_json(base / "manifest.json")
    official_path = base / manifest["official_file"]
    aliases_path = base / manifest["aliases_file"]
    dictionary = AppDictionary(official_path, aliases_path)
    if sha256(official_path) != manifest["official_sha256"]:
        raise ValueError("official dictionary hash differs from manifest")
    if sha256(aliases_path) != manifest["aliases_sha256"]:
        raise ValueError("alias dictionary hash differs from manifest")
    if dictionary.official["counts"] != manifest["expected_official_counts"]:
        raise ValueError("official counts differ from manifest")
    legacy = (base / manifest["legacy_evaluation_glossary"]).resolve()
    if sha256(legacy) != manifest["legacy_evaluation_glossary_sha256"]:
        raise ValueError("legacy evaluation glossary changed")

    survey = read_json(base / "alias-survey.json")
    active_terms = {alias["ko"] for alias in dictionary.aliases["aliases"]}
    active_terms.update(concept["ko"] for concept in dictionary.aliases["concepts"])
    missing_confirmed = sorted(
        item["term"]
        for item in survey["candidates"]
        if "active" in item["decision"] and item["term"] not in active_terms
    )
    forbidden_active = sorted(
        item["term"]
        for item in survey["candidates"]
        if item["decision"].startswith(("unconfirmed", "rejected")) and item["term"] in active_terms
    )
    if missing_confirmed or forbidden_active:
        raise ValueError(
            f"survey/active mismatch: missing={missing_confirmed}, forbidden={forbidden_active}"
        )

    alias_categories = Counter()
    statuses = Counter()
    contextual = 0
    historical = 0
    for alias in dictionary.aliases["aliases"]:
        statuses[alias["status"]] += 1
        contextual += "ambiguity" in alias
        historical += "historical" in alias
        for target_id in alias["target_ids"]:
            alias_categories[dictionary.entries[target_id]["category"]] += 1
    collisions = collision_report(dictionary)
    result: dict[str, Any] = {
        "status": "ok",
        "version": dictionary.version,
        "patch": dictionary.official["patch"],
        "dictionary_sha256": dictionary.sha256,
        "official_counts": dictionary.official["counts"],
        "official_total": len(dictionary.official["entries"]),
        "alias_count": len(dictionary.aliases["aliases"]),
        "concept_count": len(dictionary.aliases["concepts"]),
        "alias_categories": dict(sorted(alias_categories.items())),
        "alias_statuses": dict(sorted(statuses.items())),
        "context_required_aliases": contextual,
        "historical_aliases": historical,
        "collision_count": len(collisions),
        "collisions": collisions,
        "survey": {
            "candidate_count": len(survey["candidates"]),
            "decisions": dict(sorted(Counter(item["decision"] for item in survey["candidates"]).items())),
            "missing_confirmed": missing_confirmed,
            "forbidden_active": forbidden_active,
        },
        "legacy_evaluation_glossary": {
            "path": str(Path(manifest["legacy_evaluation_glossary"])),
            "sha256": sha256(legacy),
            "unchanged": True,
        },
    }
    if compare:
        result["official_diff"] = official_diff(read_json(compare), dictionary.official)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.base, args.compare)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
