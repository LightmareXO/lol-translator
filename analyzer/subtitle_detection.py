"""Image-only subtitle interval detection for the runtime analyzer.

The detector deliberately does not inspect OCR output.  It follows a binary
text-candidate mask for each manually configured line region and confirms
appearance, change and disappearance only after the candidate persists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import statistics
from typing import Any, Iterable


DEFAULT_SIMILARITY_THRESHOLD = 0.68
DEFAULT_CONFIRMATION_SAMPLES = 2
# The detail score has a different scale from the dilated overlap above.
# 0.65 is the lowest observed-data threshold that removed the known merge
# with three-sample confirmation; it remains provisional until independent
# evaluation.
DEFAULT_DETAIL_SIMILARITY_THRESHOLD = 0.65
DEFAULT_DETAIL_CONFIRMATION_SAMPLES = 3
DETECTION_VERSION = "image-line-state-machine-v2"


@dataclass(frozen=True)
class FrameFeature:
    timestamp_seconds: float
    source_pts_seconds: float
    source_index: int
    line_id: str
    line_index: int
    image: Any
    mask: Any
    present: bool
    presence_score: float
    sharpness: float
    component_count: int
    text_pixel_count: int


@dataclass(frozen=True)
class DetectedInterval:
    line_id: str
    line_index: int
    start_seconds: float
    end_seconds: float
    representative_image: Any
    representative_timestamp_seconds: float
    representative_score: float
    representative_candidates: tuple[FrameFeature, ...]
    sample_count: int
    median_similarity: float | None
    start_reason: str
    end_reason: str
    needs_review: bool


@dataclass(frozen=True)
class DroppedInterval:
    line_id: str
    line_index: int
    start_seconds: float
    end_seconds: float
    sample_count: int
    reason: str


@dataclass
class _Candidate:
    line_id: str
    line_index: int
    start_seconds: float
    start_reason: str
    reference: FrameFeature
    last: FrameFeature
    sample_count: int = 1
    similarities: list[float] = field(default_factory=list)
    representatives: list[tuple[float, FrameFeature]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.representatives.append((_representative_score(self.reference, 1.0), self.reference))

    def add(self, observation: FrameFeature, similarity: float) -> None:
        self.last = observation
        self.sample_count += 1
        self.similarities.append(similarity)
        self.representatives.append(
            (_representative_score(observation, similarity), observation)
        )
        self.representatives.sort(key=lambda item: item[0], reverse=True)
        del self.representatives[3:]


@dataclass
class _Pending:
    kind: str
    first_timestamp_seconds: float
    observations: list[FrameFeature]


def line_slices(height: int, split_ratio: float | None) -> list[tuple[str, int, int, int]]:
    """Return stable line identifiers and half-open vertical slices."""
    if height <= 0:
        raise ValueError("line image height must be positive")
    if split_ratio is None:
        return [("line-1", 0, 0, height)]
    if not math.isfinite(split_ratio) or not 0.1 <= split_ratio <= 0.9:
        raise ValueError("line split ratio must be from 0.1 to 0.9")
    split = max(1, min(height - 1, round(height * split_ratio)))
    return [("line-1", 0, 0, split), ("line-2", 1, split, height)]


def extract_text_feature(
    image: Any,
    *,
    timestamp_seconds: float,
    source_pts_seconds: float,
    source_index: int,
    line_id: str,
    line_index: int,
) -> FrameFeature:
    """Build a subtitle-like mask from color, contours and spatial layout."""
    import cv2
    import numpy as np

    if image is None or getattr(image, "ndim", 0) != 3 or image.shape[2] != 3:
        raise ValueError("line image must be a BGR image")
    height, width = image.shape[:2]
    if height < 2 or width < 2:
        raise ValueError("line image is too small")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    hue = hsv[:, :, 0]
    white = (value >= 175) & (saturation <= 120)
    yellow = (hue >= 8) & (hue <= 42) & (saturation >= 65) & (value >= 135)
    candidate = np.where(white | yellow, 255, 0).astype(np.uint8)

    minimum_height = max(4, round(height * 0.075))
    minimum_area = max(8, round(width * height * 0.00012))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(candidate, 8)
    components: list[tuple[int, int, int, int, int, float]] = []
    for label in range(1, count):
        x, y, component_width, component_height, area = [int(value) for value in stats[label]]
        if (
            component_height < minimum_height
            or component_height > height * 0.72
            or component_width < 2
            or area < minimum_area
            or component_width > width * 0.65
        ):
            continue
        components.append((label, x, y, component_width, component_height, float(centroids[label][1])))

    filtered = np.zeros_like(candidate)
    if components:
        radius = max(minimum_height * 2, round(height * 0.16))
        row_scores: list[tuple[float, float]] = []
        for component in components:
            center = component[5]
            nearby = [item for item in components if abs(item[5] - center) <= radius]
            if not nearby:
                continue
            area_score = sum(int(stats[item[0], cv2.CC_STAT_AREA]) for item in nearby)
            left = min(item[1] for item in nearby)
            right = max(item[1] + item[3] for item in nearby)
            span_bonus = 1 + (right - left) / max(1, width)
            row_scores.append((area_score * span_bonus, center))
        if row_scores:
            _, selected_center = max(row_scores)
            selected = [item for item in components if abs(item[5] - selected_center) <= radius]
            for label, *_ in selected:
                filtered[labels == label] = 255

    # A one-pixel tolerance keeps anti-aliasing and outlined glyph edges stable,
    # while the connected-component filter excludes most moving background pixels.
    filtered = cv2.dilate(filtered, np.ones((3, 3), np.uint8), iterations=1)
    component_count = 0
    horizontal_left = width
    horizontal_right = 0
    if filtered.any():
        selected_labels = set(int(value) for value in np.unique(labels[filtered > 0]) if value)
        component_count = len(selected_labels)
        positions = np.where(filtered > 0)
        horizontal_left = int(positions[1].min())
        horizontal_right = int(positions[1].max()) + 1
    text_pixels = int(np.count_nonzero(filtered))
    area_ratio = text_pixels / (width * height)
    horizontal_coverage = max(0, horizontal_right - horizontal_left) / width
    presence_score = min(1.0, area_ratio * 18 + horizontal_coverage * 0.8)
    present = (
        text_pixels >= minimum_area * 2
        and horizontal_coverage >= 0.06
        and (component_count >= 2 or horizontal_coverage >= 0.18)
    )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    maximum_width = 320
    if width > maximum_width:
        scaled_height = max(1, round(height * maximum_width / width))
        filtered = cv2.resize(
            filtered,
            (maximum_width, scaled_height),
            interpolation=cv2.INTER_NEAREST,
        )
    return FrameFeature(
        timestamp_seconds=timestamp_seconds,
        source_pts_seconds=source_pts_seconds,
        source_index=source_index,
        line_id=line_id,
        line_index=line_index,
        image=image.copy(),
        mask=filtered > 0,
        present=present,
        presence_score=presence_score,
        sharpness=sharpness,
        component_count=component_count,
        text_pixel_count=text_pixels,
    )


def mask_similarity(left: Any, right: Any) -> float:
    """Return a translation-tolerant overlap score for two binary masks."""
    import cv2
    import numpy as np

    left_mask = np.asarray(left, dtype=np.uint8)
    right_mask = np.asarray(right, dtype=np.uint8)
    if left_mask.shape != right_mask.shape:
        raise ValueError("feature masks must have equal shapes")
    left_count = int(np.count_nonzero(left_mask))
    right_count = int(np.count_nonzero(right_mask))
    if left_count == 0 and right_count == 0:
        return 1.0
    if left_count == 0 or right_count == 0:
        return 0.0
    kernel = np.ones((3, 3), np.uint8)
    left_dilated = cv2.dilate(left_mask, kernel, iterations=1) > 0
    right_dilated = cv2.dilate(right_mask, kernel, iterations=1) > 0
    matched = int(np.count_nonzero((left_mask > 0) & right_dilated))
    matched += int(np.count_nonzero((right_mask > 0) & left_dilated))
    return min(1.0, matched / (left_count + right_count))


def detail_mask_similarity(left: Any, right: Any) -> float:
    """Return exact Dice overlap with at most one pixel of global shift.

    Unlike ``mask_similarity``, this score does not dilate each mask.  It
    therefore preserves glyph-shape differences while still tolerating a
    small whole-caption position jitter.
    """
    import numpy as np

    left_mask = np.asarray(left, dtype=bool)
    right_mask = np.asarray(right, dtype=bool)
    if left_mask.shape != right_mask.shape:
        raise ValueError("feature masks must have equal shapes")
    left_count = int(np.count_nonzero(left_mask))
    right_count = int(np.count_nonzero(right_mask))
    if left_count == 0 and right_count == 0:
        return 1.0
    if left_count == 0 or right_count == 0:
        return 0.0

    height, width = left_mask.shape
    best = 0.0
    for y_shift in (-1, 0, 1):
        left_y_start = max(0, y_shift)
        left_y_end = min(height, height + y_shift)
        right_y_start = max(0, -y_shift)
        right_y_end = min(height, height - y_shift)
        for x_shift in (-1, 0, 1):
            left_x_start = max(0, x_shift)
            left_x_end = min(width, width + x_shift)
            right_x_start = max(0, -x_shift)
            right_x_end = min(width, width - x_shift)
            matched = int(
                np.count_nonzero(
                    left_mask[left_y_start:left_y_end, left_x_start:left_x_end]
                    & right_mask[
                        right_y_start:right_y_end,
                        right_x_start:right_x_end,
                    ]
                )
            )
            best = max(best, 2 * matched / (left_count + right_count))
    return best


def _representative_score(feature: FrameFeature, stability: float) -> float:
    sharpness_score = 1 - math.exp(-max(0.0, feature.sharpness) / 500)
    return 0.4 * sharpness_score + 0.35 * feature.presence_score + 0.25 * stability


class LineIntervalTracker:
    """Confirm image changes while preserving the first candidate timestamp."""

    def __init__(
        self,
        *,
        line_id: str,
        line_index: int,
        minimum_duration_seconds: float,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        confirmation_samples: int = DEFAULT_CONFIRMATION_SAMPLES,
        detail_similarity_threshold: float = DEFAULT_DETAIL_SIMILARITY_THRESHOLD,
        detail_confirmation_samples: int = DEFAULT_DETAIL_CONFIRMATION_SAMPLES,
    ) -> None:
        if minimum_duration_seconds < 0:
            raise ValueError("minimum duration must not be negative")
        if not 0 <= similarity_threshold <= 1:
            raise ValueError("similarity threshold must be from 0 to 1")
        if confirmation_samples < 1:
            raise ValueError("confirmation samples must be positive")
        if not 0 <= detail_similarity_threshold <= 1:
            raise ValueError("detail similarity threshold must be from 0 to 1")
        if detail_confirmation_samples < 1:
            raise ValueError("detail confirmation samples must be positive")
        self.line_id = line_id
        self.line_index = line_index
        self.minimum_duration_seconds = minimum_duration_seconds
        self.similarity_threshold = similarity_threshold
        self.confirmation_samples = confirmation_samples
        self.detail_similarity_threshold = detail_similarity_threshold
        self.detail_confirmation_samples = detail_confirmation_samples
        self.current: _Candidate | None = None
        self.pending: _Pending | None = None
        self.intervals: list[DetectedInterval] = []
        self.dropped: list[DroppedInterval] = []

    def _similar(self, left: FrameFeature, right: FrameFeature) -> tuple[bool, float]:
        score = mask_similarity(left.mask, right.mask)
        return score >= self.similarity_threshold, score

    def _detail_similar(
        self, left: FrameFeature, right: FrameFeature
    ) -> tuple[bool, float]:
        score = detail_mask_similarity(left.mask, right.mask)
        return score >= self.detail_similarity_threshold, score

    def _begin_candidate(
        self, observations: list[FrameFeature], *, start_reason: str
    ) -> _Candidate:
        first = observations[0]
        candidate = _Candidate(
            line_id=self.line_id,
            line_index=self.line_index,
            start_seconds=first.timestamp_seconds,
            start_reason=start_reason,
            reference=first,
            last=first,
        )
        for observation in observations[1:]:
            _, score = self._similar(candidate.reference, observation)
            candidate.add(observation, score)
        return candidate

    def _close_current(self, end_seconds: float, *, reason: str, needs_review: bool = False) -> None:
        candidate = self.current
        if candidate is None:
            return
        end_seconds = max(candidate.start_seconds, end_seconds)
        duration = end_seconds - candidate.start_seconds
        if duration + 1e-9 < self.minimum_duration_seconds:
            self.dropped.append(
                DroppedInterval(
                    line_id=candidate.line_id,
                    line_index=candidate.line_index,
                    start_seconds=candidate.start_seconds,
                    end_seconds=end_seconds,
                    sample_count=candidate.sample_count,
                    reason="shorter_than_minimum_duration",
                )
            )
        else:
            score, representative = max(candidate.representatives, key=lambda item: item[0])
            self.intervals.append(
                DetectedInterval(
                    line_id=candidate.line_id,
                    line_index=candidate.line_index,
                    start_seconds=candidate.start_seconds,
                    end_seconds=end_seconds,
                    representative_image=representative.image,
                    representative_timestamp_seconds=representative.timestamp_seconds,
                    representative_score=score,
                    representative_candidates=tuple(
                        feature for _, feature in candidate.representatives
                    ),
                    sample_count=candidate.sample_count,
                    median_similarity=(
                        statistics.median(candidate.similarities)
                        if candidate.similarities
                        else None
                    ),
                    start_reason=candidate.start_reason,
                    end_reason=reason,
                    needs_review=needs_review,
                )
            )
        self.current = None

    def add(self, observation: FrameFeature) -> None:
        if observation.line_id != self.line_id or observation.line_index != self.line_index:
            raise ValueError("observation belongs to a different line")
        if self.current is None:
            if not observation.present:
                self.pending = None
                return
            if self.confirmation_samples == 1:
                self.current = self._begin_candidate(
                    [observation],
                    start_reason="appearance_confirmed",
                )
                self.pending = None
                return
            if self.pending is None or self.pending.kind != "appearance":
                self.pending = _Pending("appearance", observation.timestamp_seconds, [observation])
                return
            similar, _ = self._similar(self.pending.observations[0], observation)
            if not similar:
                self.pending = _Pending("appearance", observation.timestamp_seconds, [observation])
                return
            self.pending.observations.append(observation)
            if len(self.pending.observations) >= self.confirmation_samples:
                self.current = self._begin_candidate(
                    self.pending.observations,
                    start_reason="appearance_confirmed",
                )
                self.pending = None
            return

        similar_to_reference = False
        detail_similar_to_reference = False
        similarity = 0.0
        if observation.present:
            similar_to_reference, similarity = self._similar(self.current.reference, observation)
            detail_similar_to_reference, _ = self._detail_similar(
                self.current.reference, observation
            )
        if observation.present and similar_to_reference and detail_similar_to_reference:
            self.current.add(observation, similarity)
            self.pending = None
            return

        if not observation.present:
            kind = "disappearance"
            required_samples = self.confirmation_samples
        elif not similar_to_reference:
            kind = "change"
            required_samples = self.confirmation_samples
        else:
            kind = "detail_change"
            required_samples = self.detail_confirmation_samples
        if required_samples == 1:
            boundary = observation.timestamp_seconds
            self._close_current(
                boundary,
                reason=(
                    "disappearance_confirmed"
                    if kind == "disappearance"
                    else f"{kind}_confirmed"
                ),
            )
            self.pending = None
            if kind != "disappearance":
                self.current = self._begin_candidate(
                    [observation],
                    start_reason=f"{kind}_confirmed",
                )
            return
        if self.pending is None or self.pending.kind != kind:
            self.pending = _Pending(kind, observation.timestamp_seconds, [observation])
            return
        if kind in {"change", "detail_change"}:
            if kind == "detail_change":
                similar_to_pending, _ = self._detail_similar(
                    self.pending.observations[0], observation
                )
            else:
                similar_to_pending, _ = self._similar(
                    self.pending.observations[0], observation
                )
            if not similar_to_pending:
                self.pending = _Pending(kind, observation.timestamp_seconds, [observation])
                return
        self.pending.observations.append(observation)
        if len(self.pending.observations) < required_samples:
            return

        boundary = self.pending.first_timestamp_seconds
        pending = self.pending
        self._close_current(
            boundary,
            reason=(
                "disappearance_confirmed"
                if kind == "disappearance"
                else f"{kind}_confirmed"
            ),
        )
        self.pending = None
        if kind != "disappearance":
            self.current = self._begin_candidate(
                pending.observations,
                start_reason=f"{kind}_confirmed",
            )

    def finish(self, range_end_seconds: float) -> None:
        if self.current is not None:
            needs_review = self.pending is not None
            self._close_current(
                range_end_seconds,
                reason=(
                    "range_end_with_unconfirmed_transition"
                    if needs_review
                    else "range_end"
                ),
                needs_review=needs_review,
            )
        elif self.pending is not None and self.pending.kind == "appearance":
            first = self.pending.observations[0]
            self.dropped.append(
                DroppedInterval(
                    line_id=self.line_id,
                    line_index=self.line_index,
                    start_seconds=first.timestamp_seconds,
                    end_seconds=range_end_seconds,
                    sample_count=len(self.pending.observations),
                    reason="appearance_not_confirmed",
                )
            )
        self.pending = None


def detect_intervals(
    observations: Iterable[FrameFeature],
    *,
    line_ids: Iterable[tuple[str, int]],
    range_end_seconds: float,
    minimum_duration_seconds: float,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    confirmation_samples: int = DEFAULT_CONFIRMATION_SAMPLES,
    detail_similarity_threshold: float = DEFAULT_DETAIL_SIMILARITY_THRESHOLD,
    detail_confirmation_samples: int = DEFAULT_DETAIL_CONFIRMATION_SAMPLES,
) -> tuple[list[DetectedInterval], list[DroppedInterval]]:
    trackers = {
        line_id: LineIntervalTracker(
            line_id=line_id,
            line_index=line_index,
            minimum_duration_seconds=minimum_duration_seconds,
            similarity_threshold=similarity_threshold,
            confirmation_samples=confirmation_samples,
            detail_similarity_threshold=detail_similarity_threshold,
            detail_confirmation_samples=detail_confirmation_samples,
        )
        for line_id, line_index in line_ids
    }
    previous_timestamp: dict[str, float] = {}
    for observation in observations:
        tracker = trackers.get(observation.line_id)
        if tracker is None:
            raise ValueError(f"unknown line id: {observation.line_id}")
        previous = previous_timestamp.get(observation.line_id)
        if previous is not None and observation.timestamp_seconds < previous:
            raise ValueError("line observations must be time ordered")
        previous_timestamp[observation.line_id] = observation.timestamp_seconds
        tracker.add(observation)
    for tracker in trackers.values():
        tracker.finish(range_end_seconds)
    intervals = sorted(
        (interval for tracker in trackers.values() for interval in tracker.intervals),
        key=lambda item: (item.start_seconds, item.line_index, item.end_seconds),
    )
    dropped = sorted(
        (interval for tracker in trackers.values() for interval in tracker.dropped),
        key=lambda item: (item.start_seconds, item.line_index, item.end_seconds),
    )
    return intervals, dropped
