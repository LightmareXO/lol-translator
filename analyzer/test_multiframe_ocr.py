import json
from pathlib import Path
import tempfile
import unittest

from ocr_evaluation.multiframe import (
    aggregate_summary,
    centered_times,
    choose_medoid,
    condition_times,
    evenly_spaced,
    file_inventory,
    nested_frame_times,
    parse_video_mapping,
    validate_specification,
    write_png,
)


BASE = Path(__file__).parent / "ocr_evaluation"


class SamplingTests(unittest.TestCase):
    def test_frame_counts_are_nested_over_the_same_span(self):
        three = nested_frame_times(10, 11, 3)
        seven = nested_frame_times(10, 11, 7)
        fifteen = nested_frame_times(10, 11, 15)
        self.assertTrue(set(three) < set(seven) < set(fifteen))

    def test_centered_span_stays_inside_stable_interval(self):
        self.assertEqual(centered_times(10, 12, 3, 1), [10.5, 11.0, 11.5])
        with self.assertRaisesRegex(ValueError, "fit inside"):
            centered_times(10, 12, 7, 3)

    def test_real_development_spec_is_valid_and_separates_axes(self):
        specification = json.loads((BASE / "multiframe_development.json").read_text(encoding="utf-8"))
        validate_specification(specification)
        conditions = condition_times(specification, specification["samples"][0])
        self.assertEqual(len(conditions["count_03_full"]), 3)
        self.assertEqual(len(conditions["count_15_full"]), 15)
        self.assertEqual(len(conditions["span_07_0p1s"]), 7)
        self.assertEqual(len(conditions["span_07_0p5s"]), 7)
        self.assertNotIn("Di7pDd0YPw4", specification["sources"])

    def test_rejects_non_development_source(self):
        specification = json.loads((BASE / "multiframe_development.json").read_text(encoding="utf-8"))
        specification["sources"]["QK95uTvf7ks"]["split"] = "final_independent_holdout"
        with self.assertRaisesRegex(ValueError, "not a development split"):
            validate_specification(specification)


class AggregationTests(unittest.TestCase):
    def test_medoid_uses_string_agreement_then_confidence(self):
        records = [
            {"frame_id": "a", "text": "볼베 궁", "confidence": 0.7},
            {"frame_id": "b", "text": "볼베궁", "confidence": 0.9},
            {"frame_id": "c", "text": "블베 공", "confidence": 0.99},
        ]
        self.assertEqual(choose_medoid(records)["frame_id"], "b")

    def test_video_mapping_rejects_duplicates_and_bad_values(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_video_mapping(["video=a.mp4", "video=b.mp4"])
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_video_mapping(["missing-separator"])

    def test_summary_reports_end_to_end_cost(self):
        metrics = {
            "characters": 2,
            "edits": 0,
            "whitespace_free_characters": 2,
            "whitespace_free_edits": 0,
            "exact": True,
            "whitespace_free_exact": True,
        }
        rows = [{
            "condition_id": "count_03_full",
            "outputs": {"medoid": {"metrics": metrics, "ocr_ms": 5, "total_pipeline_ms": 15}},
        }]
        summary = aggregate_summary(rows)["rows"][0]
        self.assertEqual(summary["ocr_ms_median"], 5)
        self.assertEqual(summary["total_pipeline_ms_median"], 15)

    def test_model_inventory_is_relative_and_hashed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nested").mkdir()
            (root / "nested" / "model.bin").write_bytes(b"model")
            inventory = file_inventory(root)
        self.assertEqual(inventory[0]["path"], "nested/model.bin")
        self.assertEqual(inventory[0]["size_bytes"], 5)
        self.assertEqual(len(inventory[0]["sha256"]), 64)


class CompositeTests(unittest.TestCase):
    def test_composites_preserve_shape_and_suppress_unstable_pixel(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
            from ocr_evaluation.multiframe import make_composite
        except ImportError:
            self.skipTest("OpenCV/NumPy are optional for standard-library tests")
        images = [np.zeros((5, 5, 3), dtype=np.uint8) for _ in range(3)]
        for image in images:
            image[2, 2] = [255, 255, 255]
        images[0][0, 0] = [255, 255, 255]
        median = make_composite(images, "temporal_median")
        masked = make_composite(images, "stable_color_edge")
        self.assertEqual(median.shape, images[0].shape)
        self.assertEqual(masked.shape, images[0].shape)
        self.assertTrue((masked[2, 2] == 255).all())
        self.assertTrue((masked[0, 0] == 0).all())

    def test_png_writer_supports_unicode_paths(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV/NumPy are optional for standard-library tests")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "韓国語字幕.png"
            write_png(path, np.zeros((2, 2, 3), dtype=np.uint8))
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
