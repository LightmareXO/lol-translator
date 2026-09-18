import copy
import json
from pathlib import Path
import tempfile
import unittest

from app_dictionary.dictionary import AppDictionary, collision_report
from app_dictionary.validate_dictionary import official_diff, validate


BASE = Path(__file__).parent / "app_dictionary"


class AppDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = AppDictionary.load_default(Path(__file__).parent)

    def test_official_counts_and_locale_pairs_are_complete(self):
        self.assertEqual(
            self.dictionary.official["counts"],
            {
                "champion": 173,
                "champion_ability": 865,
                "item": 316,
                "rune": 62,
                "rune_tree": 5,
                "summoner_spell": 34,
            },
        )
        self.assertEqual(len(self.dictionary.official["entries"]), 1455)
        self.assertTrue(
            all(entry["ko"].strip() and entry["ja"].strip() for entry in self.dictionary.official["entries"])
        )

    def test_priority_terms_select_expected_targets(self):
        cases = {
            "내셔부터 올려요": "item:3115",
            "닌탑을 사면 돼": "item:3047",
            "헤르메스 가야 돼": "item:3111",
            "구인수 나왔어요": "item:3124",
            "덤불조끼 먼저": "item:3076",
            "볼베 궁 빠졌어": "champion:Volibear",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                selected = self.dictionary.select(text)
                targets = {target for match in selected.trace["matches"] for target in match["target_ids"]}
                self.assertIn(expected, targets)

    def test_legacy_v1_curated_terms_remain_available(self):
        selected = self.dictionary.select("빙결마공점이 ㄹㅇ 좋고 요우무도 ㄱㅊ")
        targets = {
            target
            for match in selected.trace["matches"]
            for target in match["target_ids"]
        }
        self.assertTrue(
            {"rune:8306", "internet:real", "internet:okay", "item:3142"}
            <= targets
        )
        compound_matches = [
            match
            for match in selected.trace["matches"]
            if match["matched_text"] in {"빙결", "마공점"}
        ]
        self.assertTrue(compound_matches)
        self.assertTrue(
            all(match["match_type"] == "compound_segment" for match in compound_matches)
        )

    def test_contextual_short_aliases_are_registered_and_traced(self):
        selected = self.dictionary.select("다리 빙결 들고 플 쓴 다음 궁")
        targets = {target for match in selected.trace["matches"] for target in match["target_ids"]}
        self.assertTrue({"champion:Darius", "rune:8351", "summoner-spell:SummonerFlash", "game:ultimate"} <= targets)
        self.assertTrue(all(match["context_required"] for match in selected.trace["matches"] if match["matched_text"] in {"다리", "빙결", "플", "궁"}))
        self.assertIn("決めつけない", selected.prompt_text)

    def test_short_aliases_do_not_match_inside_unrelated_words(self):
        for text in (
            "플레이가 좋아요",
            "다리가 아파요",
            "궁금한 게 있어요",
            "텔레비전을 봐요",
            "볼베궁금하다",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.dictionary.select(text).trace["selected_count"], 0)

    def test_particles_and_space_omission_match_without_fuzzy_ocr(self):
        particles = self.dictionary.select("구인수는 좋고 볼베가 샀어요")
        targets = {target for match in particles.trace["matches"] for target in match["target_ids"]}
        self.assertIn("item:3124", targets)
        self.assertIn("champion:Volibear", targets)
        compact = self.dictionary.select("빙결강화를 들어요")
        match = next(match for match in compact.trace["matches"] if "rune:8351" in match["target_ids"])
        self.assertEqual(match["match_type"], "space_omitted")
        self.assertEqual(self.dictionary.select("구인소를 샀어").trace["selected_count"], 0)
        first_item = self.dictionary.select("선요우무도 괜찮아요")
        match = next(
            match
            for match in first_item.trace["matches"]
            if "item:3142" in match["target_ids"]
        )
        self.assertEqual(match["match_type"], "item_build_prefix")

    def test_known_aliases_can_form_an_exact_compound_with_korean_endings(self):
        selected = self.dictionary.select("바로 빙결다리서폿인데")
        targets = {
            target
            for match in selected.trace["matches"]
            for target in match["target_ids"]
        }
        self.assertTrue({"rune:8351", "champion:Darius", "role:support"} <= targets)
        self.assertTrue(
            all(match["match_type"] == "compound_segment" for match in selected.trace["matches"])
        )

    def test_only_source_relevant_entries_are_emitted_and_limits_apply(self):
        selected = self.dictionary.select("볼베가 닌탑을 사고 궁을 썼어")
        self.assertLessEqual(selected.trace["selected_count"], 5)
        self.assertIn("ボリベア", selected.prompt_text)
        self.assertIn("プレート スチールキャップ", selected.prompt_text)
        self.assertNotIn("グインソー", selected.prompt_text)

    def test_ability_name_requires_champion_or_slot_context(self):
        self.assertNotIn(
            "ability:Renata:P",
            {
                target
                for match in self.dictionary.select("로밍 영향력이 좋아요").trace["matches"]
                for target in match["target_ids"]
            },
        )
        contextual = self.dictionary.select("레나타 영향력 패시브")
        targets = {
            target
            for match in contextual.trace["matches"]
            for target in match["target_ids"]
        }
        self.assertIn("ability:Renata:P", targets)

    def test_unconfirmed_alias_or_missing_source_is_rejected(self):
        aliases = copy.deepcopy(self.dictionary.aliases)
        aliases["aliases"][0]["status"] = "unconfirmed"
        with tempfile.TemporaryDirectory() as temporary:
            alias_path = Path(temporary) / "aliases.json"
            alias_path.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unconfirmed alias"):
                AppDictionary(self.dictionary.official_path, alias_path)

    def test_regeneration_keeps_alias_file_independent(self):
        configuration = self.dictionary.configuration()
        self.assertNotEqual(configuration["official_sha256"], configuration["alias_sha256"])
        self.assertIn("alias_version", configuration)
        self.assertIn("official_version", configuration)

    def test_collision_report_does_not_merge_distinct_official_ids(self):
        collisions = collision_report(self.dictionary)
        frozen_heart = next(row for row in collisions if row["term"] == "얼어붙은 심장")
        self.assertEqual(frozen_heart["target_ids"], ["item:3110", "item:323110"])

    def test_manifest_validation_protects_legacy_dictionary_and_survey(self):
        report = validate(BASE)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["legacy_evaluation_glossary"]["unchanged"])
        self.assertEqual(report["survey"]["missing_confirmed"], [])
        self.assertEqual(report["survey"]["forbidden_active"], [])

    def test_comparison_dataset_selection_expectations(self):
        dataset = json.loads(
            (BASE / "comparison-dataset.json").read_text(encoding="utf-8")
        )
        self.assertFalse(dataset["holdout_used"])
        for case in dataset["cases"]:
            with self.subTest(case=case["id"]):
                selected = self.dictionary.select(case["source_ko"])
                targets = {
                    target
                    for match in selected.trace["matches"]
                    for target in match["target_ids"]
                }
                self.assertTrue(set(case.get("expected_target_ids", [])) <= targets)
                self.assertFalse(set(case.get("forbidden_target_ids", [])) & targets)

    def test_official_diff_reports_id_and_locale_name_changes(self):
        before = {"dictionary_version": "old", "entries": [{"id": "x", "ko": "옛", "ja": "旧"}]}
        after = {"dictionary_version": "new", "entries": [{"id": "x", "ko": "새", "ja": "新"}, {"id": "y", "ko": "추가", "ja": "追加"}]}
        difference = official_diff(before, after)
        self.assertEqual(difference["added_ids"], ["y"])
        self.assertEqual(difference["renamed"][0]["id"], "x")


if __name__ == "__main__":
    unittest.main()
