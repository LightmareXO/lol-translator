import copy
import json
from pathlib import Path
import tempfile
import unittest

from runtime_core import (
    PROJECT_KIND,
    SCHEMA_VERSION,
    Sample,
    apply_translation,
    atomic_write_json,
    mark_translation_stale,
    merge_samples,
    normalize_for_comparison,
    resolve_analysis_range,
    validate_project,
    validate_request,
)


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
