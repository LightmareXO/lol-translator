import unittest

import cv2
import numpy as np

from subtitle_detection import (
    FrameFeature,
    LineIntervalTracker,
    extract_text_feature,
    line_slices,
    mask_similarity,
)


def feature(timestamp, pattern, *, line_id="line-1", line_index=0, present=True):
    mask = np.zeros((20, 60), dtype=bool)
    if pattern == "a":
        mask[5:15, 10:20] = True
        mask[5:15, 25:35] = True
    elif pattern == "b":
        mask[5:15, 10:20] = True
        mask[5:15, 40:50] = True
    elif pattern == "digit-change":
        mask[5:15, 10:20] = True
        mask[2:18, 42:46] = True
    return FrameFeature(
        timestamp_seconds=timestamp,
        source_pts_seconds=timestamp,
        source_index=round(timestamp * 5),
        line_id=line_id,
        line_index=line_index,
        image=np.zeros((20, 60, 3), dtype=np.uint8),
        mask=mask,
        present=present,
        presence_score=0.8 if present else 0,
        sharpness=300 + timestamp,
        component_count=2 if present else 0,
        text_pixel_count=int(mask.sum()),
    )


class ImageFeatureTests(unittest.TestCase):
    def test_line_slices_keep_stable_reading_order(self):
        self.assertEqual(line_slices(100, None), [("line-1", 0, 0, 100)])
        self.assertEqual(
            line_slices(100, 0.4),
            [("line-1", 0, 0, 40), ("line-2", 1, 40, 100)],
        )

    def test_mask_similarity_tolerates_one_pixel_shift_but_detects_change(self):
        left = feature(0, "a").mask
        shifted = np.roll(left, 1, axis=1)
        self.assertGreater(mask_similarity(left, shifted), 0.9)
        self.assertLess(mask_similarity(left, feature(0, "b").mask), 0.68)

    def test_feature_uses_white_and_yellow_text_but_not_plain_background(self):
        image = np.full((80, 320, 3), (40, 70, 40), dtype=np.uint8)
        cv2.putText(image, "TEST", (80, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 4)
        white = extract_text_feature(
            image,
            timestamp_seconds=1,
            source_pts_seconds=1,
            source_index=5,
            line_id="line-1",
            line_index=0,
        )
        image[:] = (40, 70, 40)
        cv2.putText(image, "TEST", (80, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 220, 255), 4)
        yellow = extract_text_feature(
            image,
            timestamp_seconds=1,
            source_pts_seconds=1,
            source_index=5,
            line_id="line-1",
            line_index=0,
        )
        image[:] = (40, 70, 40)
        empty = extract_text_feature(
            image,
            timestamp_seconds=1,
            source_pts_seconds=1,
            source_index=5,
            line_id="line-1",
            line_index=0,
        )
        self.assertTrue(white.present)
        self.assertTrue(yellow.present)
        self.assertFalse(empty.present)

    def test_gradual_fade_keeps_one_interval_until_text_becomes_unreadable(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
        )
        observations = []
        for index, intensity in enumerate((255, 220, 190, 160, 140)):
            image = np.full((80, 320, 3), (40, 70, 40), dtype=np.uint8)
            cv2.putText(
                image,
                "TEST",
                (80, 52),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.5,
                (intensity, intensity, intensity),
                4,
            )
            observations.append(
                extract_text_feature(
                    image,
                    timestamp_seconds=index * 0.2,
                    source_pts_seconds=index * 0.2,
                    source_index=index,
                    line_id="line-1",
                    line_index=0,
                )
            )
        self.assertEqual(
            [observation.present for observation in observations],
            [True, True, True, False, False],
        )
        for observation in observations:
            tracker.add(observation)
        tracker.finish(1.0)
        self.assertEqual(len(tracker.intervals), 1)
        self.assertEqual(tracker.intervals[0].start_seconds, 0.0)
        self.assertAlmostEqual(tracker.intervals[0].end_seconds, 0.6)


class LineTrackerTests(unittest.TestCase):
    def test_single_sample_confirmation_closes_and_reopens_immediately(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
            confirmation_samples=1,
        )
        tracker.add(feature(0.0, "a"))
        tracker.add(feature(0.2, "b"))
        tracker.add(feature(0.4, "", present=False))
        tracker.finish(0.6)
        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in tracker.intervals],
            [(0.0, 0.2), (0.2, 0.4)],
        )

    def test_boundaries_keep_first_candidate_timestamp(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
            confirmation_samples=2,
        )
        observations = [
            feature(0.0, "", present=False),
            feature(0.2, "a"),
            feature(0.4, "a"),
            feature(0.6, "a"),
            feature(0.8, "b"),
            feature(1.0, "b"),
            feature(1.2, "b"),
            feature(1.4, "", present=False),
            feature(1.6, "", present=False),
        ]
        for observation in observations:
            tracker.add(observation)
        tracker.finish(1.8)
        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in tracker.intervals],
            [(0.2, 0.8), (0.8, 1.4)],
        )

    def test_one_frame_background_change_does_not_split(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
            confirmation_samples=2,
        )
        for timestamp, pattern in [
            (0.0, "a"),
            (0.2, "a"),
            (0.4, "a"),
            (0.6, "b"),
            (0.8, "a"),
            (1.0, "a"),
        ]:
            tracker.add(feature(timestamp, pattern))
        tracker.finish(1.2)
        self.assertEqual(len(tracker.intervals), 1)
        self.assertEqual(tracker.intervals[0].start_seconds, 0)
        self.assertEqual(tracker.intervals[0].end_seconds, 1.2)

    def test_short_confirmed_interval_is_recorded_as_dropped(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=1.2,
            confirmation_samples=2,
        )
        for timestamp, pattern, present in [
            (0.0, "a", True),
            (0.2, "a", True),
            (0.4, "a", True),
            (0.6, "", False),
            (0.8, "", False),
        ]:
            tracker.add(feature(timestamp, pattern, present=present))
        tracker.finish(1.0)
        self.assertEqual(tracker.intervals, [])
        self.assertEqual(len(tracker.dropped), 1)
        self.assertEqual(tracker.dropped[0].reason, "shorter_than_minimum_duration")

    def test_lines_change_independently(self):
        upper = LineIntervalTracker(
            line_id="line-1", line_index=0, minimum_duration_seconds=0
        )
        lower = LineIntervalTracker(
            line_id="line-2", line_index=1, minimum_duration_seconds=0
        )
        for timestamp in (0, 0.2, 0.4, 0.6):
            upper.add(feature(timestamp, "a"))
            lower.add(feature(timestamp, "a" if timestamp < 0.4 else "b", line_id="line-2", line_index=1))
        upper.finish(0.8)
        lower.finish(0.8)
        self.assertEqual(len(upper.intervals), 1)
        self.assertEqual(len(lower.intervals), 2)

    def test_same_text_reappearing_after_confirmed_gap_is_a_new_interval(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
        )
        for timestamp, pattern, present in [
            (0.0, "a", True),
            (0.2, "a", True),
            (0.4, "", False),
            (0.6, "", False),
            (0.8, "a", True),
            (1.0, "a", True),
        ]:
            tracker.add(feature(timestamp, pattern, present=present))
        tracker.finish(1.2)
        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in tracker.intervals],
            [(0.0, 0.4), (0.8, 1.2)],
        )

    def test_one_character_shape_change_is_not_merged(self):
        tracker = LineIntervalTracker(
            line_id="line-1",
            line_index=0,
            minimum_duration_seconds=0,
        )
        for timestamp, pattern in [
            (0.0, "a"),
            (0.2, "a"),
            (0.4, "digit-change"),
            (0.6, "digit-change"),
        ]:
            tracker.add(feature(timestamp, pattern))
        tracker.finish(0.8)
        self.assertEqual(len(tracker.intervals), 2)
        self.assertEqual(tracker.intervals[1].start_seconds, 0.4)


if __name__ == "__main__":
    unittest.main()
