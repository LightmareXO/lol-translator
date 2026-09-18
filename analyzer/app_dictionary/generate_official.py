"""Generate paired Korean/Japanese official terminology from Riot Data Dragon."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


DEFAULT_PATCH = "16.18.1"
LOCALES = ("ko_KR", "ja_JP")
BASE_URL = "https://ddragon.leagueoflegends.com/cdn/{patch}/data/{locale}"
STATIC_FILES = ("champion.json", "item.json", "runesReforged.json", "summoner.json")
TAG_RE = re.compile(r"<[^>]+>")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class SourceStore:
    def __init__(self, patch: str, cache: Path):
        self.patch = patch
        self.cache = cache
        self.records: dict[str, dict[str, str]] = {}

    def get(self, locale: str, relative: str) -> dict | list:
        key = f"{locale}/{relative}"
        url = f"{BASE_URL.format(patch=self.patch, locale=locale)}/{relative}"
        destination = self.cache / locale / relative
        if destination.exists():
            raw = destination.read_bytes()
        else:
            request = Request(url, headers={"User-Agent": "lol-translator-dictionary-generator/1"})
            with urlopen(request, timeout=60) as response:
                raw = response.read()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        self.records[key] = {"url": url, "sha256": sha256_bytes(raw)}
        return json.loads(raw)


def paired_ids(ko: dict, ja: dict, label: str) -> list[str]:
    ko_ids = set(ko)
    ja_ids = set(ja)
    if ko_ids != ja_ids:
        missing_ja = sorted(ko_ids - ja_ids)
        missing_ko = sorted(ja_ids - ko_ids)
        raise ValueError(f"{label} locale ID mismatch: missing_ja={missing_ja}, missing_ko={missing_ko}")
    return sorted(ko_ids)


def clean_name(value: str) -> str:
    return TAG_RE.sub("", value).replace("<br>", " ").strip()


def official_entry(category: str, identifier: str, ko: str, ja: str, **extra: object) -> dict:
    if not ko.strip() or not ja.strip():
        raise ValueError(f"empty official name: {category}:{identifier}")
    return {
        "category": category,
        "id": identifier,
        "ko": clean_name(ko),
        "ja": clean_name(ja),
        **extra,
    }


def generate(patch: str, retrieved_at: str, cache: Path) -> dict:
    store = SourceStore(patch, cache)
    raw: dict[str, dict[str, dict | list]] = {locale: {} for locale in LOCALES}
    for locale in LOCALES:
        for filename in STATIC_FILES:
            raw[locale][filename] = store.get(locale, filename)

    entries: list[dict] = []
    ko_champions = raw["ko_KR"]["champion.json"]["data"]
    ja_champions = raw["ja_JP"]["champion.json"]["data"]
    champion_ids = paired_ids(ko_champions, ja_champions, "champion")
    for champion_id in champion_ids:
        entries.append(
            official_entry(
                "champion",
                f"champion:{champion_id}",
                ko_champions[champion_id]["name"],
                ja_champions[champion_id]["name"],
                official_id=champion_id,
            )
        )
        details = {
            locale: store.get(locale, f"champion/{champion_id}.json")["data"][champion_id]
            for locale in LOCALES
        }
        if len(details["ko_KR"]["spells"]) != 4 or len(details["ja_JP"]["spells"]) != 4:
            raise ValueError(f"expected four spells for {champion_id}")
        entries.append(
            official_entry(
                "champion_ability",
                f"ability:{champion_id}:P",
                details["ko_KR"]["passive"]["name"],
                details["ja_JP"]["passive"]["name"],
                champion_id=champion_id,
                slot="P",
            )
        )
        for slot, ko_spell, ja_spell in zip(
            "QWER", details["ko_KR"]["spells"], details["ja_JP"]["spells"], strict=True
        ):
            entries.append(
                official_entry(
                    "champion_ability",
                    f"ability:{champion_id}:{slot}",
                    ko_spell["name"],
                    ja_spell["name"],
                    champion_id=champion_id,
                    slot=slot,
                    source_spell_id=ko_spell.get("id"),
                )
            )

    ko_items = raw["ko_KR"]["item.json"]["data"]
    ja_items = raw["ja_JP"]["item.json"]["data"]
    for item_id in paired_ids(ko_items, ja_items, "item"):
        ko_item = ko_items[item_id]
        if not ko_item.get("maps", {}).get("11", False):
            continue
        ja_item = ja_items[item_id]
        entries.append(
            official_entry(
                "item",
                f"item:{item_id}",
                ko_item["name"],
                ja_item["name"],
                official_id=item_id,
                map_id=11,
                purchasable=bool(ko_item.get("gold", {}).get("purchasable", False)),
                in_store=ko_item.get("inStore") is not False,
                required_champion=ko_item.get("requiredChampion"),
                special_recipe=ko_item.get("specialRecipe"),
            )
        )

    ko_trees = raw["ko_KR"]["runesReforged.json"]
    ja_trees = raw["ja_JP"]["runesReforged.json"]
    ko_tree_by_id = {str(tree["id"]): tree for tree in ko_trees}
    ja_tree_by_id = {str(tree["id"]): tree for tree in ja_trees}
    for tree_id in paired_ids(ko_tree_by_id, ja_tree_by_id, "rune tree"):
        ko_tree = ko_tree_by_id[tree_id]
        ja_tree = ja_tree_by_id[tree_id]
        entries.append(
            official_entry(
                "rune_tree",
                f"rune-tree:{tree_id}",
                ko_tree["name"],
                ja_tree["name"],
                official_id=tree_id,
            )
        )
        ko_runes = {str(rune["id"]): rune for slot in ko_tree["slots"] for rune in slot["runes"]}
        ja_runes = {str(rune["id"]): rune for slot in ja_tree["slots"] for rune in slot["runes"]}
        for rune_id in paired_ids(ko_runes, ja_runes, f"runes in tree {tree_id}"):
            entries.append(
                official_entry(
                    "rune",
                    f"rune:{rune_id}",
                    ko_runes[rune_id]["name"],
                    ja_runes[rune_id]["name"],
                    official_id=rune_id,
                    tree_id=tree_id,
                )
            )

    ko_spells = raw["ko_KR"]["summoner.json"]["data"]
    ja_spells = raw["ja_JP"]["summoner.json"]["data"]
    for spell_id in paired_ids(ko_spells, ja_spells, "summoner spell"):
        entries.append(
            official_entry(
                "summoner_spell",
                f"summoner-spell:{spell_id}",
                ko_spells[spell_id]["name"],
                ja_spells[spell_id]["name"],
                official_id=spell_id,
                modes=sorted(ko_spells[spell_id].get("modes", [])),
            )
        )

    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate generated dictionary IDs")
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return {
        "schema_version": 1,
        "dictionary_version": f"lol-ko-ja-official-{patch}",
        "patch": patch,
        "retrieved_at": retrieved_at,
        "scope": {
            "champions": "all Data Dragon champions",
            "items": "all item IDs marked for Summoner's Rift map 11; availability flags retained",
            "runes": "all rune trees and runes",
            "summoner_spells": "all Data Dragon summoner spells; modes retained",
            "champion_abilities": "passive and Q/W/E/R for every champion, paired by champion ID and slot",
        },
        "sources": {key: store.records[key] for key in sorted(store.records)},
        "counts": counts,
        "entries": sorted(entries, key=lambda entry: (entry["category"], entry["id"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", default=DEFAULT_PATCH)
    parser.add_argument("--retrieved-at", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, generate(args.patch, args.retrieved_at, args.cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
