import { describe, expect, it } from "vitest";
import {
  activeSubtitle,
  effectiveJapanese,
  type SubtitleRecord,
  updateCorrectedKorean,
  updateUserJapanese,
} from "./analysisProject";

function subtitle(id: string, start: number, end: number): SubtitleRecord {
  return {
    id,
    start_seconds: start,
    end_seconds: end,
    image_png_base64: null,
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
