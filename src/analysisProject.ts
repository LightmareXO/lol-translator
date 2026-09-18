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
  line_id: string;
  line_index: number;
  start_seconds: number;
  end_seconds: number;
  image_png_base64: string | null;
  detection: {
    status: "confirmed" | "needs_review" | "legacy";
    start_reason: string;
    end_reason: string;
    needs_review: boolean;
    [key: string]: unknown;
  };
  ocr: {
    status: "completed" | "error";
    raw_text: string;
    raw_lines: string[];
    confidence: number | null;
    error: string | null;
    attempts?: Array<Record<string, unknown>>;
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
  schema_version: 2;
  kind: "lol-translator-project";
  source_video: {
    path: string;
    name: string;
    size_bytes: number;
    modified_unix_ms: number;
    sha256: string;
    duration_seconds: number;
    start_time_seconds?: number;
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
    minimum_display_duration_ms: number;
  };
  configuration: {
    detection: Record<string, unknown>;
    ocr: Record<string, unknown>;
    translation: Record<string, unknown>;
  };
  subtitles: SubtitleRecord[];
  relationships: {
    simultaneous: Array<Record<string, unknown>>;
  };
  processing: {
    state: "completed" | "completed_with_errors";
    sample_count: number;
    subtitle_count: number;
    detection_count: number;
    dropped_intervals: Array<Record<string, unknown>>;
    ocr_call_count: number;
    translation_call_count: number;
    errors: Array<Record<string, unknown>>;
  };
}

type LegacySubtitleRecord = Omit<
  SubtitleRecord,
  "line_id" | "line_index" | "detection"
> & {
  line_id?: string;
  line_index?: number;
  detection?: SubtitleRecord["detection"];
};

interface LegacyAnalysisProject
  extends Omit<
    AnalysisProject,
    | "schema_version"
    | "analysis"
    | "configuration"
    | "subtitles"
    | "relationships"
    | "processing"
  > {
  schema_version: 1;
  analysis: Omit<AnalysisProject["analysis"], "minimum_display_duration_ms"> & {
    minimum_display_duration_ms?: number;
  };
  configuration: Omit<AnalysisProject["configuration"], "detection"> & {
    detection?: Record<string, unknown>;
  };
  subtitles: LegacySubtitleRecord[];
  relationships?: AnalysisProject["relationships"];
  processing: Omit<
    AnalysisProject["processing"],
    | "detection_count"
    | "dropped_intervals"
    | "ocr_call_count"
    | "translation_call_count"
  > & {
    detection_count?: number;
    dropped_intervals?: Array<Record<string, unknown>>;
    ocr_call_count?: number;
    translation_call_count?: number;
  };
}

export function migrateAnalysisProject(
  value: AnalysisProject | LegacyAnalysisProject,
): AnalysisProject {
  if (value.schema_version === 2) return value;
  const subtitles = value.subtitles.map((subtitle) => ({
    ...subtitle,
    line_id: subtitle.line_id ?? "line-1",
    line_index: subtitle.line_index ?? 0,
    detection: subtitle.detection ?? {
      status: "legacy" as const,
      start_reason: "legacy_saved_boundary",
      end_reason: "legacy_saved_boundary",
      needs_review: false,
    },
    ocr: {
      ...subtitle.ocr,
      attempts: subtitle.ocr.attempts ?? [],
    },
  }));
  return {
    ...value,
    schema_version: 2,
    analysis: {
      ...value.analysis,
      minimum_display_duration_ms:
        value.analysis.minimum_display_duration_ms ?? 1200,
    },
    configuration: {
      ...value.configuration,
      detection: value.configuration.detection ?? {
        version: "legacy-ocr-text-timeline-v1",
        boundary_source: "ocr_text",
        migrated_from_schema_version: 1,
      },
    },
    subtitles,
    relationships: value.relationships ?? { simultaneous: [] },
    processing: {
      ...value.processing,
      detection_count:
        value.processing.detection_count ?? value.subtitles.length,
      dropped_intervals: value.processing.dropped_intervals ?? [],
      ocr_call_count:
        value.processing.ocr_call_count ?? value.processing.sample_count,
      translation_call_count:
        value.processing.translation_call_count ?? value.subtitles.length,
    },
  };
}

export function activeSubtitles(
  subtitles: SubtitleRecord[],
  timeSeconds: number,
): SubtitleRecord[] {
  return subtitles
    .filter(
      (subtitle) =>
        subtitle.start_seconds <= timeSeconds &&
        timeSeconds < subtitle.end_seconds,
    )
    .sort(
      (left, right) =>
        left.line_index - right.line_index ||
        left.start_seconds - right.start_seconds ||
        left.id.localeCompare(right.id),
    );
}

export function activeSubtitle(
  subtitles: SubtitleRecord[],
  timeSeconds: number,
): SubtitleRecord | null {
  return activeSubtitles(subtitles, timeSeconds)[0] ?? null;
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
