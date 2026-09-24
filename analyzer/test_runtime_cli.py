import unittest

import numpy as np

from runtime_cli import _recognize_interval
from subtitle_detection import DetectedInterval, FrameFeature


def candidate(timestamp: float) -> FrameFeature:
    return FrameFeature(
        timestamp_seconds=timestamp,
        source_pts_seconds=timestamp,
        source_index=round(timestamp * 5),
        line_id="line-1",
        line_index=0,
        image=np.zeros((20, 60, 3), dtype=np.uint8),
        mask=np.ones((20, 60), dtype=bool),
        present=True,
        presence_score=1,
        sharpness=100,
        component_count=3,
        text_pixel_count=1200,
    )


def interval() -> DetectedInterval:
    candidates = tuple(candidate(value) for value in (0.2, 0.4, 0.6))
    return DetectedInterval(
        line_id="line-1",
        line_index=0,
        start_seconds=0.2,
        end_seconds=1.2,
        representative_image=candidates[0].image,
        representative_timestamp_seconds=0.2,
        representative_score=1,
        representative_candidates=candidates,
        sample_count=5,
        median_similarity=0.9,
        start_reason="appearance_confirmed",
        end_reason="disappearance_confirmed",
        needs_review=False,
    )


class FakeRecognizer:
    def __init__(self, results):
        self.results = iter(results)

    def recognize(self, _image):
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class IntervalOcrTests(unittest.TestCase):
    def test_retries_low_information_result_without_creating_another_interval(self):
        recognizer = FakeRecognizer([("", None), ("정상 자막", 0.91)])
        ocr, _image, calls = _recognize_interval(interval(), recognizer)
        self.assertEqual(calls, 2)
        self.assertEqual(ocr["status"], "completed")
        self.assertEqual(ocr["raw_text"], "정상 자막")
        self.assertEqual(len(ocr["attempts"]), 2)

    def test_records_one_error_after_at_most_three_failed_candidates(self):
        recognizer = FakeRecognizer(
            [("", None), RuntimeError("failed"), ("?", 0.1)]
        )
        ocr, _image, calls = _recognize_interval(interval(), recognizer)
        self.assertEqual(calls, 3)
        self.assertEqual(ocr["status"], "error")
        self.assertEqual(len(ocr["attempts"]), 3)


if __name__ == "__main__":
    unittest.main()
