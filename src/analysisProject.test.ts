import { describe, expect, it } from "vitest";
import {
  type AnalysisProject,
  activeSubtitle,
  activeSubtitles,
  effectiveJapanese,
  migrateAnalysisProject,
  type SubtitleRecord,
  updateCorrectedKorean,
  updateUserJapanese,
} from "./analysisProject";

function subtitle(id: string, start: number, end: number): SubtitleRecord {
  return {
    id,
    line_id: "line-1",
    line_index: 0,
    start_seconds: start,
    end_seconds: end,
    image_png_base64: null,
    detection: {
      status: "confirmed",
      start_reason: "test",
      end_reason: "test",
      needs_review: false,
    },
    ocr: {
      status: "completed",
      raw_text: "원문",
      raw_lines: ["원문"],
      confidence: 0.9,
      error: null,
    },
    corrected_ko: null,
    translation: {
      status: "completed",
      source_ko: "원문",
      generated_ja: "自動訳",
      user_ja: null,
      error: null,
    },
  };
}

describe("subtitle timing", () => {
  const subtitles = [subtitle("a", 1, 2), subtitle("b", 3, 4)];

  it("uses a closed start and open end boundary", () => {
    expect(activeSubtitle(subtitles, 1)?.id).toBe("a");
    expect(activeSubtitle(subtitles, 1.999)?.id).toBe("a");
    expect(activeSubtitle(subtitles, 2)).toBeNull();
    expect(activeSubtitle(subtitles, 3)?.id).toBe("b");
  });

  it("returns no subtitle in gaps or outside the analyzed range", () => {
    expect(activeSubtitle(subtitles, 0)).toBeNull();
    expect(activeSubtitle(subtitles, 2.5)).toBeNull();
    expect(activeSubtitle(subtitles, 4)).toBeNull();
  });

  it("returns overlapping lines in reading order", () => {
    const upper = subtitle("upper", 1, 3);
    const lower = {
      ...subtitle("lower", 1.5, 2.5),
      line_id: "line-2",
      line_index: 1,
    };
    expect(activeSubtitles([lower, upper], 2).map((item) => item.id)).toEqual([
      "upper",
      "lower",
    ]);
  });
});

it("keeps raw OCR and marks translation stale after a Korean correction", () => {
  const original = subtitle("a", 1, 2);
  const corrected = updateCorrectedKorean(original, "수정문");
  expect(corrected.ocr.raw_text).toBe("원문");
  expect(corrected.corrected_ko).toBe("수정문");
  expect(corrected.translation.status).toBe("stale");
});

it("restores the completed state when a Korean correction is reverted", () => {
  const original = subtitle("a", 1, 2);
  const corrected = updateCorrectedKorean(original, "수정문");
  const reverted = updateCorrectedKorean(corrected, "");
  expect(reverted.corrected_ko).toBeNull();
  expect(reverted.translation.status).toBe("completed");
});

it("prefers a user Japanese edit and restores generated text when cleared", () => {
  const original = subtitle("a", 1, 2);
  const edited = updateUserJapanese(original, "ユーザー訳");
  expect(effectiveJapanese(edited)).toBe("ユーザー訳");
  expect(effectiveJapanese(updateUserJapanese(edited, ""))).toBe("自動訳");
});

it("migrates schema v1 without changing boundaries or user edits", () => {
  const item = subtitle("legacy", 1.2, 3.4);
  item.translation.user_ja = "人手で直した訳";
  const legacy = {
    schema_version: 1,
    kind: "lol-translator-project",
    source_video: {
      path: "C:/test.mp4",
      name: "test.mp4",
      size_bytes: 1,
      modified_unix_ms: 1,
      sha256: "abc",
      duration_seconds: 10,
      width: 1920,
      height: 1080,
    },
    analysis: {
      mode: "range",
      start_seconds: 0,
      end_seconds: 10,
      subtitle_region: { x: 0, y: 0.7, width: 1, height: 0.3 },
      sample_interval_ms: 200,
      line_split_ratio: null,
    },
    configuration: { ocr: {}, translation: {} },
    subtitles: [
      {
        ...item,
        line_id: undefined,
        line_index: undefined,
        detection: undefined,
      },
    ],
    processing: {
      state: "completed",
      sample_count: 50,
      subtitle_count: 1,
      errors: [],
    },
  } as unknown as Parameters<typeof migrateAnalysisProject>[0];

  const migrated: AnalysisProject = migrateAnalysisProject(legacy);
  expect(migrated.schema_version).toBe(2);
  expect(migrated.subtitles[0]).toMatchObject({
    start_seconds: 1.2,
    end_seconds: 3.4,
    line_id: "line-1",
    line_index: 0,
    translation: { user_ja: "人手で直した訳" },
  });
  expect(migrated.analysis.minimum_display_duration_ms).toBe(1200);
  expect(migrated.processing.ocr_call_count).toBe(50);
});
