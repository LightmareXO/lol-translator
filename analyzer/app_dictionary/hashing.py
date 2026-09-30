"""Stable hashes for JSON dictionary documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


HASH_ALGORITHM = "sha256-canonical-json-v1"


def canonical_json_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def json_file_sha256(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    return canonical_json_sha256(value)
