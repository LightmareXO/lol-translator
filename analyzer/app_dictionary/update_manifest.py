"""Refresh app dictionary file hashes and expected record counts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    from hashing import HASH_ALGORITHM, json_file_sha256
else:
    from .hashing import HASH_ALGORITHM, json_file_sha256


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def update_manifest(base: Path) -> dict[str, Any]:
    manifest_path = base / "manifest.json"
    manifest = read_json(manifest_path)
    items = [
        manifest["official_file"],
        manifest["sources_file"],
        *manifest["alias_files"],
        *manifest["term_files"],
    ]
    for item in items:
        path = base / item["path"]
        document = read_json(path)
        item["sha256"] = json_file_sha256(path)
        if item["role"] == "official":
            item["expected_count"] = len(document["entries"])
        elif item["role"] == "sources":
            item["expected_count"] = len(document["sources"])
        elif item["role"] == "aliases":
            item["expected_count"] = len(document["aliases"])
        elif item["role"] == "terms":
            item["expected_concept_count"] = len(document.get("concepts", []))
            item["expected_alias_count"] = len(document.get("aliases", []))
        else:
            raise ValueError(f"unknown dictionary file role: {item['role']}")
    manifest["schema_version"] = 3
    manifest["hash_algorithm"] = HASH_ALGORITHM
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    update_manifest(Path(__file__).resolve().parent)
