import unittest

from detection_evaluation.evaluate import evaluate_intervals


class DetectionEvaluationTests(unittest.TestCase):
    def test_one_to_one_matching_does_not_count_duplicate_predictions_twice(self):
        truth = [
            {
                "id": "truth",
                "line_id": "line-1",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
            }
        ]
        predicted = [
            {
                "id": "first",
                "line_id": "line-1",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
            },
            {
                "id": "duplicate",
                "line_id": "line-1",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
            },
        ]
        result = evaluate_intervals(truth, predicted)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["oversegmentation_extra_count"], 1)

    def test_matching_keeps_independent_lines_separate(self):
        truth = [
            {
                "id": "upper",
                "line_id": "line-1",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
            }
        ]
        predicted = [
            {
                "id": "lower",
                "line_id": "line-2",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
            }
        ]
        result = evaluate_intervals(truth, predicted)
        self.assertEqual(result["matched_count"], 0)
        self.assertEqual(result["unmatched_truth_ids"], ["upper"])


if __name__ == "__main__":
    unittest.main()
