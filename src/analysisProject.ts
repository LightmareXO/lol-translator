import type { SubtitleRegion } from "./subtitleRegion";

export interface TranslationRecord {
  status: "pending" | "completed" | "stale" | "error";
  source_ko: string;
  generated_ja: string | null;
  user_ja: string | null;
  error: string | null;
}

export interface SubtitleRecord {
  id: string;
  start_seconds: number;
  end_seconds: number;
  image_png_base64: string | null;
  ocr: {
    status: "completed";
    raw_text: string;
    raw_lines: string[];
    confidence: number | null;
    error: string | null;
    variants?: Array<{
      start_seconds: number;
      end_seconds: number;
      raw_text: string;
      raw_lines: string[];
      confidence: number | null;
    }>;
  };
  corrected_ko: string | null;
  translation: TranslationRecord;
}

export interface AnalysisProject {
  schema_version: 1;
  kind: "lol-translator-project";
  source_video: {
    path: string;
    name: string;
    size_bytes: number;
    modified_unix_ms: number;
    sha256: string;
    duration_seconds: number;
    width: number;
    height: number;
  };
  analysis: {
    mode: "range" | "whole";
    start_seconds: number;
    end_seconds: number;
    subtitle_region: SubtitleRegion;
    sample_interval_ms: number;
    line_split_ratio: number | null;
  };
  configuration: {
    ocr: Record<string, unknown>;
    translation: Record<string, unknown>;
  };
  subtitles: SubtitleRecord[];
  processing: {
    state: "completed" | "completed_with_errors";
    sample_count: number;
    subtitle_count: number;
    errors: Array<Record<string, unknown>>;
  };
}

export function activeSubtitle(
  subtitles: SubtitleRecord[],
  timeSeconds: number,
): SubtitleRecord | null {
  let low = 0;
  let high = subtitles.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const subtitle = subtitles[middle];
    if (timeSeconds < subtitle.start_seconds) {
      high = middle - 1;
    } else if (timeSeconds >= subtitle.end_seconds) {
      low = middle + 1;
    } else {
      return subtitle;
    }
  }
  return null;
}

export function effectiveJapanese(subtitle: SubtitleRecord): string {
  return subtitle.translation.user_ja?.trim()
    ? subtitle.translation.user_ja
    : (subtitle.translation.generated_ja ?? "");
}

export function effectiveKorean(subtitle: SubtitleRecord): string {
  return subtitle.corrected_ko?.trim()
    ? subtitle.corrected_ko
    : subtitle.ocr.raw_text;
}

export function updateCorrectedKorean(
  subtitle: SubtitleRecord,
  value: string,
): SubtitleRecord {
  const corrected = value.trim() ? value : null;
  const effective = corrected ?? subtitle.ocr.raw_text;
  const sourceMatches = subtitle.translation.source_ko === effective;
  const status = sourceMatches
    ? subtitle.translation.status === "stale"
      ? subtitle.translation.generated_ja
        ? "completed"
        : "pending"
      : subtitle.translation.status
    : "stale";
  return {
    ...subtitle,
    corrected_ko: corrected,
    translation: {
      ...subtitle.translation,
      status,
    },
  };
}

export function updateUserJapanese(
  subtitle: SubtitleRecord,
  value: string,
): SubtitleRecord {
  return {
    ...subtitle,
    translation: {
      ...subtitle.translation,
      user_ja: value.length > 0 ? value : null,
    },
  };
}

export function replaceSubtitle(
  project: AnalysisProject,
  subtitle: SubtitleRecord,
): AnalysisProject {
  return {
    ...project,
    subtitles: project.subtitles.map((item) =>
      item.id === subtitle.id ? subtitle : item,
    ),
  };
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.0";
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${remainder}`;
}
