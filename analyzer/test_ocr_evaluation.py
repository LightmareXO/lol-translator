import copy
import csv
import json
from pathlib import Path
import unittest

from ocr_evaluation.core import (
    edit_distance,
    load_manifest,
    normalize_text,
    normalized_region_to_pixels,
    percentile,
    summarize_records,
    validate_manifest,
)
from ocr_evaluation.aggregate import dataset_summary, validate_result


MANIFEST_PATH = Path(__file__).parent / "ocr_evaluation" / "manifest.json"
RESULTS_PATH = Path(__file__).parent / "ocr_evaluation" / "results"


class ManifestTests(unittest.TestCase):
    def test_real_manifest_is_valid_and_pixel_aligned(self):
        manifest = load_manifest(MANIFEST_PATH)
        self.assertEqual(len(manifest["images"]), 24)
        self.assertEqual(
            normalized_region_to_pixels(
                manifest["source"]["subtitle_region"],
                manifest["source"]["width"],
                manifest["source"]["height"],
            ),
            (38, 862, 1844, 96),
        )

    def test_rejects_unverified_or_inconsistent_truth(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        unverified = copy.deepcopy(manifest)
        unverified["images"][0]["ground_truth_status"] = "draft"
        with self.assertRaisesRegex(ValueError, "must be verified"):
            validate_manifest(unverified)
        inconsistent = copy.deepcopy(manifest)
        inconsistent["images"][1]["ground_truth"] = "different"
        with self.assertRaisesRegex(ValueError, "inconsistent ground truth"):
            validate_manifest(inconsistent)

    def test_rejects_paths_that_can_escape_image_directory(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest["images"][0]["image_file"] = "../video.mp4"
        with self.assertRaisesRegex(ValueError, "plain file name"):
            validate_manifest(manifest)


class MetricTests(unittest.TestCase):
    def test_nfc_normalization_and_edit_distance(self):
        decomposed = "한글"
        self.assertEqual(normalize_text(decomposed), "한글")
        self.assertEqual(edit_distance("한글 자막", "한굴자막"), 2)

    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([1, 2, 3], 0.5), 2)
        self.assertAlmostEqual(percentile([1, 2, 3], 0.95), 2.9)

    def test_summary_counts_failures_without_dropping_them(self):
        records = [
            {
                "image_id": "a",
                "group_id": "g1",
                "timestamp_seconds": 1.0,
                "ground_truth": "한 글",
                "raw_output": "한 글",
                "error": None,
                "inference_ms": 10,
                "total_ms": 12,
            },
            {
                "image_id": "b",
                "group_id": "g1",
                "timestamp_seconds": 1.1,
                "ground_truth": "한 글",
                "raw_output": "",
                "error": "inference failed",
                "inference_ms": None,
                "total_ms": None,
            },
        ]
        summary = summarize_records(records)
        self.assertEqual(summary["image_count"], 2)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["exact_matches"], 1)
        self.assertEqual(summary["reference_characters"], 6)
        self.assertAlmostEqual(summary["cer"], 0.5)
        self.assertEqual(summary["stability"]["groups"][0]["changed_pairs"], 1)


class AggregationTests(unittest.TestCase):
    def test_dataset_summary_counts_images_and_groups_separately(self):
        manifest = load_manifest(MANIFEST_PATH)
        summary = dataset_summary(manifest)
        self.assertEqual(summary["image_count"], 24)
        self.assertEqual(summary["subtitle_group_count"], 8)
        self.assertEqual(summary["conditions"]["one_line"], {
            "image_count": 24,
            "subtitle_group_count": 8,
        })

    def test_result_must_match_manifest_order_and_digest(self):
        manifest = load_manifest(MANIFEST_PATH)
        digest = __import__("hashlib").sha256(
            MANIFEST_PATH.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        result = {
            "schema_version": 1,
            "manifest_sha256": digest,
            "metadata": {"preprocessing": "raw"},
            "records": [
                {
                    "image_id": item["id"],
                    "group_id": item["group_id"],
                    "timestamp_seconds": item["timestamp_seconds"],
                    "tags": item["tags"],
                    "line_count": item["line_count"],
                    "ground_truth": item["ground_truth"],
                }
                for item in manifest["images"]
            ],
        }
        validate_result(result, Path("result.json"), manifest, digest)
        result["records"].reverse()
        with self.assertRaisesRegex(ValueError, "image order"):
            validate_result(result, Path("result.json"), manifest, digest)

    def test_committed_results_are_complete_and_path_free(self):
        with (RESULTS_PATH / "details.csv").open(
            encoding="utf-8-sig", newline=""
        ) as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 192)
        self.assertEqual(len({(row["engine"], row["preprocessing"]) for row in rows}), 8)
        self.assertEqual(len({row["image_id"] for row in rows}), 24)
        self.assertTrue(all(not row["error"] for row in rows))
        for path in RESULTS_PATH.iterdir():
            if path.suffix in {".csv", ".json", ".md"}:
                contents = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("C:\\Users\\", contents)


if __name__ == "__main__":
    unittest.main()
