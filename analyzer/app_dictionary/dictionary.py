"""Load, validate, and select source-relevant LoL dictionary candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import unicodedata
from typing import Any


ACTIVE_STATUSES = (
    "source_checked",
    "community_source_checked",
    "official_derived",
)
KOREAN_PARTICLES = tuple(
    sorted(
        (
            "이라고", "라고", "에게", "한테", "으로", "에서", "부터", "까지", "처럼",
            "보다", "하고", "이랑", "이나", "은", "는", "이", "가", "을", "를", "에",
            "로", "와", "과", "도", "만", "의", "랑", "나",
        ),
        key=len,
        reverse=True,
    )
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _is_boundary(value: str, index: int) -> bool:
    return index < 0 or index >= len(value) or not value[index].isalnum()


def _match_end(value: str, end: int) -> int | None:
    if _is_boundary(value, end):
        return end
    for particle in KOREAN_PARTICLES:
        particle_end = end + len(particle)
        if value.startswith(particle, end) and _is_boundary(value, particle_end):
            return end
    return None


def _find_bounded(value: str, needle: str) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    offset = 0
    while (index := value.find(needle, offset)) >= 0:
        end = index + len(needle)
        if _is_boundary(value, index - 1) and _match_end(value, end) is not None:
            matches.append((index, end))
        offset = index + 1
    return matches


def _find_space_insensitive(value: str, needle: str) -> list[tuple[int, int]]:
    compact_needle = "".join(character for character in needle if not character.isspace())
    matches: list[tuple[int, int]] = []
    for start in range(len(value)):
        if not _is_boundary(value, start - 1):
            continue
        source_index = start
        needle_index = 0
        while source_index < len(value) and needle_index < len(compact_needle):
            if value[source_index].isspace():
                source_index += 1
                continue
            if value[source_index] != compact_needle[needle_index]:
                break
            source_index += 1
            needle_index += 1
        if needle_index == len(compact_needle) and _match_end(value, source_index) is not None:
            matches.append((start, source_index))
    return matches


@dataclass(frozen=True)
class DictionarySelection:
    prompt_text: str
    trace: dict[str, Any]


class AppDictionary:
    def __init__(self, official_path: Path, aliases_path: Path):
        self.official_path = official_path
        self.aliases_path = aliases_path
        self.official = _read_json(official_path)
        self.aliases = _read_json(aliases_path)
        self.entries: dict[str, dict[str, Any]] = {}
        for entry in self.official["entries"]:
            self.entries[entry["id"]] = {**entry, "origin": "official"}
        for concept in self.aliases["concepts"]:
            if concept["id"] in self.entries:
                raise ValueError(f"duplicate dictionary ID: {concept['id']}")
            self.entries[concept["id"]] = {**concept, "origin": "curated_concept"}
        self.validate()
        self.official_sha256 = _sha256(official_path)
        self.aliases_sha256 = _sha256(aliases_path)
        identity = f"{self.official_sha256}:{self.aliases_sha256}".encode()
        self.sha256 = hashlib.sha256(identity).hexdigest()
        self.version = (
            f"lol-ko-ja-app-{self.official['patch']}-{self.aliases['checked_at']}"
        )

    @classmethod
    def load_default(cls, analyzer_root: Path) -> "AppDictionary":
        directory = analyzer_root / "app_dictionary"
        return cls(directory / "official-16.18.1.json", directory / "aliases.json")

    def validate(self) -> None:
        official_ids = [entry["id"] for entry in self.official["entries"]]
        if len(official_ids) != len(set(official_ids)):
            raise ValueError("duplicate official dictionary ID")
        alias_ids = [alias["id"] for alias in self.aliases["aliases"]]
        if len(alias_ids) != len(set(alias_ids)):
            raise ValueError("duplicate alias ID")
        sources = self.aliases["sources"]
        for alias in self.aliases["aliases"]:
            if not alias["status"].startswith(ACTIVE_STATUSES):
                raise ValueError(f"unconfirmed alias in active dictionary: {alias['id']}")
            if not alias.get("sources"):
                raise ValueError(f"alias has no source: {alias['id']}")
            unknown_sources = set(alias["sources"]) - set(sources)
            if unknown_sources:
                raise ValueError(f"unknown alias source {unknown_sources}: {alias['id']}")
            unknown_targets = set(alias["target_ids"]) - set(self.entries)
            if unknown_targets:
                raise ValueError(f"unknown alias target {unknown_targets}: {alias['id']}")
            if len(alias["ko"].strip()) < 2 and "ambiguity" not in alias:
                raise ValueError(f"one-character alias must record ambiguity: {alias['id']}")
        expected: dict[str, int] = {}
        for entry in self.official["entries"]:
            expected[entry["category"]] = expected.get(entry["category"], 0) + 1
            if not entry["ko"].strip() or not entry["ja"].strip():
                raise ValueError(f"missing locale name: {entry['id']}")
        if expected != self.official["counts"]:
            raise ValueError("official category counts do not match entries")

    def configuration(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "patch": self.official["patch"],
            "sha256": self.sha256,
            "official_version": self.official["dictionary_version"],
            "official_sha256": self.official_sha256,
            "alias_version": self.aliases["version"],
            "alias_sha256": self.aliases_sha256,
            "selection_mode": "bounded-source-relevant-v2",
        }

    def _term_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for entry in self.entries.values():
            records.append(
                {
                    "term": entry["ko"],
                    "target_ids": [entry["id"]],
                    "source_id": entry["id"],
                    "source_type": "official_name" if entry["origin"] == "official" else "concept_name",
                    "status": "official" if entry["origin"] == "official" else "curated",
                    "ambiguity": None,
                    "compact": " " in entry["ko"] and len(entry["ko"].replace(" ", "")) >= 4,
                }
            )
        for alias in self.aliases["aliases"]:
            records.append(
                {
                    "term": alias["ko"],
                    "target_ids": alias["target_ids"],
                    "source_id": alias["id"],
                    "source_type": alias["type"],
                    "status": alias["status"],
                    "ambiguity": alias.get("ambiguity"),
                    "historical": alias.get("historical"),
                    "compact": False,
                }
            )
        return records

    def _matches(self, source_text: str) -> list[dict[str, Any]]:
        source = _normalized(source_text)
        matches: list[dict[str, Any]] = []
        for record in self._term_records():
            term = _normalized(record["term"]).strip()
            if record.get("ambiguity") and any(
                _normalized(cue) in source
                for cue in record["ambiguity"].get("negative_cues", [])
            ):
                continue
            spans = _find_bounded(source, term)
            match_type = "exact"
            if not spans and record["compact"]:
                spans = _find_space_insensitive(source, term)
                match_type = "space_omitted"
            for start, end in spans:
                matches.append(
                    {
                        **record,
                        "matched_text": source_text[start:end],
                        "start": start,
                        "end": end,
                        "match_type": match_type,
                    }
                )
        matches.sort(
            key=lambda match: (
                match["start"],
                -(match["end"] - match["start"]),
                match["source_type"] != "official_name",
                match["source_id"],
            )
        )
        return matches

    def select(self, source_text: str, *, max_entries: int = 8, max_characters: int = 1800) -> DictionarySelection:
        if not isinstance(source_text, str):
            raise TypeError("source text must be a string")
        matches = self._matches(source_text)
        short_limit = 5 if len(source_text) <= 80 else max_entries
        entry_limit = min(max_entries, short_limit)
        selected: list[dict[str, Any]] = []
        seen_targets: set[tuple[str, ...]] = set()
        for match in matches:
            target_key = tuple(match["target_ids"])
            if target_key in seen_targets:
                continue
            if any(
                match["start"] >= existing["start"]
                and match["end"] <= existing["end"]
                and set(match["target_ids"]) == set(existing["target_ids"])
                for existing in selected
            ):
                continue
            selected.append(match)
            seen_targets.add(target_key)
            if len(selected) >= entry_limit:
                break

        lines: list[str] = []
        trace_matches: list[dict[str, Any]] = []
        used_characters = 0
        for match in selected:
            targets = [self.entries[target_id] for target_id in match["target_ids"]]
            target_text = " / ".join(
                f"{target['ko']} = {target['ja']}（{target.get('meaning', target['category'])}）"
                for target in targets
            )
            condition = ""
            if ambiguity := match.get("ambiguity"):
                alternatives = "、".join(ambiguity.get("other_meanings", []))
                condition = f" 文脈条件: {ambiguity['guidance']} 他の意味候補: {alternatives}."
            if historical := match.get("historical"):
                condition += (
                    f" 旧称由来: {historical.get('origin_ko', '不明')}。"
                    "動画時期が不明なら現行名だったと断定しない。"
                )
            line = (
                f"[{','.join(match['target_ids'])}] 原文一致「{match['matched_text']}」: "
                f"{target_text}.{condition}"
            )
            if used_characters + len(line) > max_characters:
                break
            lines.append(line)
            used_characters += len(line) + 1
            trace_matches.append(
                {
                    "target_ids": match["target_ids"],
                    "source_id": match["source_id"],
                    "source_type": match["source_type"],
                    "status": match["status"],
                    "matched_text": match["matched_text"],
                    "start": match["start"],
                    "end": match["end"],
                    "match_type": match["match_type"],
                    "context_required": match.get("ambiguity") is not None,
                }
            )
        prompt = ""
        if lines:
            prompt = (
                "以下は原文に一致したLoL用語候補だけです。原文と文脈に合う意味だけを使い、"
                "根拠が足りない曖昧語を特定のLoL用語に決めつけないでください。"
                "候補一覧そのものを訳文へ出力しないでください。\n" + "\n".join(lines)
            )
        trace = {
            "dictionary_version": self.version,
            "dictionary_sha256": self.sha256,
            "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "selected_count": len(trace_matches),
            "matches": trace_matches,
            "omitted_match_count": max(0, len(matches) - len(trace_matches)),
            "limits": {"entries": entry_limit, "characters": max_characters},
        }
        return DictionarySelection(prompt_text=prompt, trace=trace)


def collision_report(dictionary: AppDictionary) -> list[dict[str, Any]]:
    by_term: dict[str, set[str]] = {}
    source_ids: dict[str, list[str]] = {}
    for record in dictionary._term_records():
        term = _normalized(record["term"]).strip()
        by_term.setdefault(term, set()).update(record["target_ids"])
        source_ids.setdefault(term, []).append(record["source_id"])
    return [
        {"term": term, "target_ids": sorted(targets), "source_ids": sorted(source_ids[term])}
        for term, targets in sorted(by_term.items())
        if len(targets) > 1
    ]
