import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_core import (
    PROJECT_KIND,
    SCHEMA_VERSION,
    Sample,
    apply_translation,
    are_conservative_ocr_variants,
    are_temporal_ocr_variants,
    atomic_write_json,
    is_low_information_ocr,
    mark_translation_stale,
    merge_samples,
    normalize_for_comparison,
    resolve_analysis_range,
    stabilize_subtitles,
    validate_project,
    validate_request,
)
from runtime_cli import OCR_PREPROCESSING


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "한국어 動画.mp4"
        self.video.touch()
        self.request = {
            "schema_version": 1,
            "video_path": str(self.video),
            "subtitle_region": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
            "analysis_range": {"mode": "range", "start_seconds": 60, "end_seconds": 960},
            "settings": {"sample_interval_ms": 200, "line_split_ratio": None},
        }

    def test_range_has_no_three_minute_limit(self):
        checked = validate_request(self.request)
        self.assertEqual(resolve_analysis_range(checked, 1_000), (60.0, 960.0))

    def test_runtime_uses_the_evaluated_otsu_preprocessing(self):
        self.assertEqual(OCR_PREPROCESSING, "otsu")

    def test_whole_video_resolves_to_metadata_duration(self):
        self.request["analysis_range"] = {
            "mode": "whole",
            "start_seconds": None,
            "end_seconds": None,
        }
        checked = validate_request(self.request)
        self.assertEqual(resolve_analysis_range(checked, 901.25), (0.0, 901.25))

    def test_rejects_bad_range_interval_and_manual_split(self):
        variants = [
            ("analysis_range", {"mode": "range", "start_seconds": 2, "end_seconds": 1}),
            ("analysis_range", {"mode": "whole", "start_seconds": 0, "end_seconds": None}),
            ("settings", {"sample_interval_ms": 49, "line_split_ratio": None}),
            ("settings", {"sample_interval_ms": 200, "line_split_ratio": 0.95}),
        ]
        for field, value in variants:
            with self.subTest(field=field, value=value):
                request = copy.deepcopy(self.request)
                request[field] = value
                with self.assertRaises(ValueError):
                    validate_request(request)

    def test_atomic_utf8_round_trip(self):
        destination = self.root / "result.json"
        atomic_write_json(destination, {"ko": "안녕", "ja": "こんにちは"})
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8")),
            {"ko": "안녕", "ja": "こんにちは"},
        )
        self.assertFalse(destination.with_suffix(".json.tmp").exists())

    def test_atomic_write_retries_a_transient_windows_replace_conflict(self):
        destination = self.root / "progress.json"
        real_replace = os.replace
        attempts = 0

        def replace_after_conflicts(source, target):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError("temporarily locked")
            real_replace(source, target)

        with patch("runtime_core.os.replace", side_effect=replace_after_conflicts):
            atomic_write_json(destination, {"state": "running", "current": 44})

        self.assertEqual(attempts, 3)
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8"))["current"], 44
        )


class TimelineTests(unittest.TestCase):
    def test_merges_only_adjacent_whitespace_equivalent_text(self):
        samples = [
            Sample(10.0, "subtitle", "우리 팀 2", ("우리 팀 2",), 0.9, "a"),
            Sample(10.2, "subtitle", "우리\n팀 2", ("우리", "팀 2"), 0.8, "b"),
            Sample(10.4, "subtitle", "우리 팀 3", ("우리 팀 3",), 0.9, "c"),
            Sample(10.6, "no_subtitle"),
            Sample(10.8, "subtitle", "우리 팀 3", ("우리 팀 3",), 0.9, "d"),
        ]
        subtitles, errors = merge_samples(samples, interval_seconds=0.2, range_end_seconds=11)
        self.assertEqual(errors, [])
        self.assertEqual([row["ocr"]["raw_text"] for row in subtitles], ["우리 팀 2", "우리 팀 3", "우리 팀 3"])
        self.assertEqual([(row["start_seconds"], row["end_seconds"]) for row in subtitles], [(10.0, 10.4), (10.4, 10.6), (10.8, 11.0)])
        self.assertEqual(subtitles[0]["image_png_base64"], "a")
        self.assertEqual(subtitles[0]["ocr"]["raw_lines"], ["우리 팀 2"])

    def test_ocr_error_closes_caption_and_is_recorded_separately(self):
        subtitles, errors = merge_samples(
            [
                Sample(1.0, "subtitle", "가자"),
                Sample(1.2, "ocr_error", error="decode failed"),
                Sample(1.4, "subtitle", "가자"),
            ],
            interval_seconds=0.2,
            range_end_seconds=2,
        )
        self.assertEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0]["end_seconds"], 1.2)
        self.assertEqual(errors[0]["timestamp_seconds"], 1.2)
        self.assertEqual(errors[0]["message"], "decode failed")

    def test_unicode_normalization_does_not_hide_character_changes(self):
        self.assertEqual(normalize_for_comparison("가 \n 나"), normalize_for_comparison("가나"))
        self.assertNotEqual(normalize_for_comparison("스킬 Q"), normalize_for_comparison("스킬 E"))
        self.assertNotEqual(normalize_for_comparison("2명"), normalize_for_comparison("3명"))

    def test_low_information_filter_rejects_uncertain_non_korean_noise(self):
        self.assertTrue(is_low_information_ocr("S", 0.9))
        self.assertTrue(is_low_information_ocr("SZHT", 0.33))
        self.assertTrue(is_low_information_ocr("?", 0.95))
        self.assertFalse(is_low_information_ocr("GG", 0.9))
        self.assertTrue(is_low_information_ocr("가", 0.2))
        self.assertFalse(is_low_information_ocr("가", 0.8))

    def test_conservative_variants_protect_numbers_and_latin_skill_letters(self):
        self.assertTrue(
            are_conservative_ocr_variants("책프폭이상득 씬데?", "책프폭이상들씬데?")
        )
        self.assertFalse(are_conservative_ocr_variants("스킬 Q 사용", "스킬 E 사용"))
        self.assertFalse(are_conservative_ocr_variants("적 2명", "적 3명"))

    def test_temporal_variants_ignore_edge_noise_but_protect_conflicting_tokens(self):
        self.assertTrue(
            are_temporal_ocr_variants(
                "cY바로마공점빛망 준비해습니다",
                "바로 마공점빛망 준비해습니다",
            )
        )
        self.assertTrue(
            are_temporal_ocr_variants(
                "트 진짜 객쩌는 받드 가져온",
                "브는 진짜 개쩌는 빌드 가져온",
            )
        )
        self.assertFalse(are_temporal_ocr_variants("스킬 Q 사용합니다", "스킬 E 사용합니다"))
        self.assertFalse(are_temporal_ocr_variants("적이 2명 있어요", "적이 3명 있어요"))

    def test_stabilization_keeps_variants_and_uses_the_highest_confidence_text(self):
        subtitles = merge_samples(
            [
                Sample(1.0, "subtitle", "책프폭이상득 씬데?", confidence=0.7),
                Sample(1.2, "subtitle", "책프폭이상들씬데?", confidence=0.9),
                Sample(1.4, "subtitle", "스킬 Q 사용", confidence=0.8),
                Sample(1.6, "subtitle", "스킬 E 사용", confidence=0.8),
            ],
            interval_seconds=0.2,
            range_end_seconds=1.8,
        )[0]
        stabilized = stabilize_subtitles(
            subtitles, maximum_gap_seconds=0, minimum_duration_seconds=0
        )
        self.assertEqual(len(stabilized), 3)
        self.assertEqual(stabilized[0]["start_seconds"], 1.0)
        self.assertEqual(stabilized[0]["end_seconds"], 1.4)
        self.assertEqual(stabilized[0]["ocr"]["raw_text"], "책프폭이상들씬데?")
        self.assertEqual(len(stabilized[0]["ocr"]["variants"]), 2)
        self.assertEqual(
            [item["id"] for item in stabilized],
            ["subtitle-00001", "subtitle-00002", "subtitle-00003"],
        )

    def test_stabilization_bridges_one_missing_sample_and_drops_brief_noise(self):
        subtitles = merge_samples(
            [
                Sample(1.0, "subtitle", "cY바로 마공점 준비합니다", confidence=0.7),
                Sample(1.2, "subtitle", "바로 마공점 준비합니다", confidence=0.9),
                Sample(1.4, "no_subtitle"),
                Sample(1.6, "subtitle", "바로 마공점 준비합니다", confidence=0.8),
                Sample(1.8, "subtitle", "바로 마공점 준비합니다", confidence=0.8),
                Sample(2.0, "subtitle", "바로 마공점 준비합니다", confidence=0.8),
                Sample(2.2, "no_subtitle"),
                Sample(2.4, "subtitle", "전혀 다른 문장", confidence=0.8),
            ],
            interval_seconds=0.2,
            range_end_seconds=2.6,
        )[0]

        stabilized = stabilize_subtitles(subtitles, maximum_gap_seconds=0.2)

        self.assertEqual(len(stabilized), 1)
        self.assertEqual(stabilized[0]["start_seconds"], 1.0)
        self.assertEqual(stabilized[0]["end_seconds"], 2.2)
        self.assertEqual(stabilized[0]["ocr"]["raw_text"], "바로 마공점 준비합니다")
        self.assertEqual(len(stabilized[0]["ocr"]["variants"]), 3)

    def test_stabilization_keeps_stable_captions_with_different_skill_letters(self):
        samples = [
            Sample(1.0 + index * 0.2, "subtitle", "스킬 Q 사용합니다", confidence=0.9)
            for index in range(6)
        ] + [
            Sample(2.2 + index * 0.2, "subtitle", "스킬 E 사용합니다", confidence=0.9)
            for index in range(6)
        ]
        subtitles = merge_samples(
            samples, interval_seconds=0.2, range_end_seconds=3.4
        )[0]

        stabilized = stabilize_subtitles(subtitles, maximum_gap_seconds=0.2)

        self.assertEqual(
            [item["ocr"]["raw_text"] for item in stabilized],
            ["스킬 Q 사용합니다", "스킬 E 사용합니다"],
        )

    def test_corrections_mark_stale_and_retranslation_preserves_user_japanese(self):
        subtitle = merge_samples(
            [Sample(1, "subtitle", "원문")], interval_seconds=0.2, range_end_seconds=2
        )[0][0]
        subtitle["translation"]["user_ja"] = "ユーザー訳"
        apply_translation(subtitle, "自動訳")
        mark_translation_stale(subtitle, "수정문")
        self.assertEqual(subtitle["translation"]["status"], "stale")
        apply_translation(subtitle, "再翻訳", source_ko="수정문")
        self.assertEqual(subtitle["translation"]["generated_ja"], "再翻訳")
        self.assertEqual(subtitle["translation"]["user_ja"], "ユーザー訳")

    def test_reverting_korean_correction_restores_completed_translation(self):
        subtitle = merge_samples(
            [Sample(1, "subtitle", "원문")], interval_seconds=0.2, range_end_seconds=2
        )[0][0]
        apply_translation(subtitle, "自動訳")
        mark_translation_stale(subtitle, "수정문")
        mark_translation_stale(subtitle, None)
        self.assertEqual(subtitle["corrected_ko"], None)
        self.assertEqual(subtitle["translation"]["status"], "completed")

    def test_project_validation_rejects_overlapping_or_duplicate_subtitles(self):
        subtitles = merge_samples(
            [Sample(1, "subtitle", "A"), Sample(1.2, "subtitle", "B")],
            interval_seconds=0.2,
            range_end_seconds=2,
        )[0]
        project = {"schema_version": SCHEMA_VERSION, "kind": PROJECT_KIND, "subtitles": subtitles}
        self.assertIs(validate_project(project), project)
        project["subtitles"][1]["id"] = project["subtitles"][0]["id"]
        with self.assertRaises(ValueError):
            validate_project(project)


if __name__ == "__main__":
    unittest.main()
