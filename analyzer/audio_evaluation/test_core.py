import json
from pathlib import Path
import tempfile
import unittest

from analyzer.audio_evaluation.build_correspondence import build
from analyzer.audio_evaluation.core import (
    directory_size,
    load_json,
    save_json,
    sha256_file,
    source_time,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parent


class ManifestTests(unittest.TestCase):
    def test_committed_manifest_is_valid(self):
        manifest = load_json(ROOT / "manifest.json")
        validate_manifest(manifest)
        self.assertEqual(9, len(manifest["samples"]))
        self.assertEqual(10, sum(len(sample["lines"]) for sample in manifest["samples"]))

    def test_t15_lines_are_kept_separate(self):
        manifest = load_json(ROOT / "manifest.json")
        sample = next(item for item in manifest["samples"] if item["id"] == "t15")
        self.assertEqual(
            ["t15_annotation_f02", "t15_speech_f02"],
            [line["subtitle_id"] for line in sample["lines"]],
        )
        self.assertEqual(["annotation", "speech"], [line["role"] for line in sample["lines"]])

    def test_duplicate_subtitle_id_is_rejected(self):
        manifest = load_json(ROOT / "manifest.json")
        manifest["samples"][1]["lines"][0]["subtitle_id"] = manifest["samples"][0]["lines"][0]["subtitle_id"]
        with self.assertRaisesRegex(ValueError, "duplicate subtitle id"):
            validate_manifest(manifest)

    def test_insufficient_padding_is_rejected(self):
        manifest = load_json(ROOT / "manifest.json")
        manifest["samples"][0]["audio_start_seconds"] = manifest["samples"][0]["display_start_seconds"] - 1
        with self.assertRaisesRegex(ValueError, "insufficient audio padding"):
            validate_manifest(manifest)

    def test_path_like_sample_id_is_rejected(self):
        manifest = load_json(ROOT / "manifest.json")
        manifest["samples"][0]["id"] = "../escape"
        with self.assertRaisesRegex(ValueError, "unsafe sample id"):
            validate_manifest(manifest)


class HelperTests(unittest.TestCase):
    def test_source_time_does_not_confuse_clip_and_video_offsets(self):
        self.assertEqual(370.125, source_time(366.25, 3.875))

    def test_directory_size_counts_nested_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "nested").mkdir()
            (root / "a").write_bytes(b"123")
            (root / "nested" / "b").write_bytes(b"4567")
            self.assertEqual(7, directory_size(root))


class CorrespondenceTests(unittest.TestCase):
    def test_unconfirmed_candidate_is_not_counted_as_confirmed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = root / "manifest.json"
            transcripts_path = root / "transcripts.json"
            reviews_path = root / "reviews.json"
            manifest = {
                "schema_version": 1,
                "audio_padding_seconds": 2,
                "sources": {"video": {"duration_seconds": 20}},
                "samples": [
                    {
                        "id": "sample",
                        "video_id": "video",
                        "display_start_seconds": 5,
                        "display_end_seconds": 7,
                        "audio_start_seconds": 3,
                        "audio_end_seconds": 9,
                        "lines": [
                            {"subtitle_id": "confirmed", "role": "speech", "color": "white", "text_ko": "가", "reference_status": "verified"},
                            {"subtitle_id": "candidate", "role": "annotation", "color": "yellow", "text_ko": "나", "reference_status": "verified"},
                        ],
                    }
                ],
            }
            save_json(manifest_path, manifest)
            save_json(
                transcripts_path,
                {
                    "manifest_sha256": sha256_file(manifest_path),
                    "rows": [
                        {"sample_id": "sample", "raw_text": " 가", "segments": [{"source_start_seconds": 5.0, "source_end_seconds": 6.0, "text": " 가"}]}
                    ],
                },
            )
            save_json(
                reviews_path,
                {
                    "reviews": [
                        {"subtitle_id": "confirmed", "classification": "near_verbatim", "candidate_category": None, "discrepancy": "none", "confirmation_status": "confirmed_by_listening", "evidence": "heard"},
                        {"subtitle_id": "candidate", "classification": "unresolved", "candidate_category": "unspoken_supplement", "discrepancy": "candidate", "confirmation_status": "unconfirmed_no_audio_input", "evidence": "not heard"},
                    ]
                },
            )
            summary = build(
                manifest_path,
                transcripts_path,
                reviews_path,
                root / "output",
            )
        self.assertEqual(2, summary["line_count"])
        self.assertEqual(1, summary["confirmed_counts"]["near_verbatim"])
        self.assertEqual(1, summary["unconfirmed_candidate_counts"]["unspoken_supplement"])


if __name__ == "__main__":
    unittest.main()
