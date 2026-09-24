import json
from pathlib import Path
import unittest

import numpy as np

from ocr_evaluation.tesseract_pipeline import (
    PREPROCESSING_MODES,
    join_line_outputs,
    preprocess_image,
    score_records,
    stability_summary,
    split_lines,
    strip_cli_trailing_newlines,
)
from ocr_evaluation.background_comparison import chosen_modes


SPEC_PATH = Path(__file__).parent / "ocr_evaluation" / "tesseract_evaluation.json"
RESULTS_PATH = Path(__file__).parent / "ocr_evaluation" / "results"


class TextPolicyTests(unittest.TestCase):
    def test_cli_newline_policy_preserves_internal_line_breaks(self):
        self.assertEqual(strip_cli_trailing_newlines("一行目\r\n二行目\r\n"), "一行目\r\n二行目")
        self.assertEqual(join_line_outputs(["上\r\n", "下\n"]), "上\n下")

    def test_nfc_and_failure_are_scored_without_exclusion(self):
        records = [
            {
                "ground_truth": "한 글",
                "raw_output": "한 글",
                "error": None,
                "inference_ms": 1,
                "total_ms": 2,
            },
            {
                "ground_truth": "자막",
                "raw_output": "",
                "error": "process failed",
                "inference_ms": None,
                "total_ms": 3,
            },
        ]
        summary = score_records(records)
        self.assertEqual(summary["image_count"], 2)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["empty_output_count"], 1)
        self.assertEqual(summary["exact_matches"], 1)
        self.assertEqual(summary["edit_distance"], 2)
        self.assertEqual(summary["whitespace_free_exact_matches"], 1)

    def test_adjacent_frame_changes_are_separate_from_accuracy_counts(self):
        records = [
            {"group_id": "g1", "timestamp_seconds": 1.0, "raw_output": "가"},
            {"group_id": "g1", "timestamp_seconds": 1.1, "raw_output": "가"},
            {"group_id": "g1", "timestamp_seconds": 1.2, "raw_output": "나"},
            {"group_id": "single", "timestamp_seconds": 2.0, "raw_output": "다"},
        ]
        summary = stability_summary(records)
        self.assertEqual(summary["group_count"], 1)
        self.assertEqual(summary["adjacent_pair_count"], 2)
        self.assertEqual(summary["changed_pair_count"], 1)
        self.assertEqual(summary["change_rate"], 0.5)


class ImagePolicyTests(unittest.TestCase):
    def test_line_bands_are_joined_in_fixed_top_to_bottom_order(self):
        image = np.zeros((6, 4, 3), dtype=np.uint8)
        image[:2] = 10
        image[2:] = 20
        lines = split_lines(image, [[0, 2], [2, 6]])
        self.assertEqual([line.shape[0] for line in lines], [2, 4])
        self.assertEqual(int(lines[0][0, 0, 0]), 10)
        self.assertEqual(int(lines[1][0, 0, 0]), 20)

    def test_all_seven_preprocessing_modes_keep_image_size(self):
        image = np.zeros((41, 81, 3), dtype=np.uint8)
        image[:, 20:60] = 220
        outputs = {
            mode: preprocess_image(
                image, mode, adaptive_block_size=21, adaptive_c=7
            )
            for mode in PREPROCESSING_MODES
        }
        self.assertEqual(len(outputs), 7)
        self.assertTrue(all(output.shape == image.shape for output in outputs.values()))

    def test_binarization_uses_dark_text_on_light_background_polarity(self):
        image = np.full((41, 81, 3), 30, dtype=np.uint8)
        image[:, 30:50] = 230
        output = preprocess_image(
            image, "otsu", adaptive_block_size=21, adaptive_c=7
        )
        self.assertEqual(int(output[20, 40, 0]), 0)
        self.assertEqual(int(output[20, 5, 0]), 255)

    def test_invalid_adaptive_block_size_is_rejected(self):
        image = np.zeros((5, 5, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "odd integer"):
            preprocess_image(image, "adaptive", adaptive_block_size=20, adaptive_c=7)


class FrozenSpecificationTests(unittest.TestCase):
    def test_split_and_candidate_set_were_fixed(self):
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        self.assertFalse(spec["independent_evaluation"]["available"])
        self.assertEqual(spec["reference_policy"]["human_confirmed_count"], 0)
        self.assertEqual(len(spec["adaptive_tuning"]["candidates"]), 3)
        self.assertEqual(spec["main_preprocessing"], list(PREPROCESSING_MODES))
        self.assertEqual(spec["background_suppression"]["frame_count"], 7)
        self.assertEqual(spec["background_suppression"]["span_seconds"], 0.5)

    def test_background_comparison_uses_each_engines_fixed_best_mode(self):
        summary = {
            "configurations": [
                {"engine_key": "paddle", "preprocessing": "original", "representative": {"cer": 0.2}},
                {"engine_key": "paddle", "preprocessing": "otsu", "representative": {"cer": 0.1}},
                {"engine_key": "tesseract_fast", "preprocessing": "grayscale", "representative": {"cer": 0.5}},
                {"engine_key": "tesseract_fast", "preprocessing": "otsu", "representative": {"cer": 0.6}},
            ]
        }
        self.assertEqual(
            chosen_modes(summary),
            {"paddle": "otsu", "tesseract_fast": "grayscale"},
        )

    def test_committed_results_cover_the_fixed_comparison_without_local_paths(self):
        dataset = json.loads(
            (RESULTS_PATH / "tesseract-dataset.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (RESULTS_PATH / "tesseract-summary.json").read_text(encoding="utf-8")
        )
        details = json.loads(
            (RESULTS_PATH / "tesseract-details.json").read_text(encoding="utf-8")
        )
        background = json.loads(
            (RESULTS_PATH / "tesseract-background.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(dataset["cases"]), 28)
        self.assertEqual(sum(case["representative"] for case in dataset["cases"]), 12)
        self.assertTrue(all(len(case["crop_xywh"]) == 4 for case in dataset["cases"]))
        self.assertTrue(all(case["prior_development_use"] for case in dataset["cases"]))
        self.assertTrue(all(case["reference_verifier"] for case in dataset["cases"]))
        self.assertEqual(len(summary["configurations"]), 21)
        self.assertEqual(len(details), 28 * 7 * 3)
        self.assertEqual(len(background["records"]), 9 * 3 * 3)
        self.assertFalse(summary["independent_evaluation"])
        self.assertEqual(summary["human_confirmed_subtitles"], 0)
        for path in RESULTS_PATH.glob("tesseract-*.json"):
            self.assertNotIn("C:\\Users\\", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
