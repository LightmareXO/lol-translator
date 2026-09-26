import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AnalysisProject, SubtitleRecord } from "./analysisProject";
import { PlaybackSubtitlePanel } from "./PlaybackSubtitlePanel";

afterEach(cleanup);

function subtitle(overrides: Partial<SubtitleRecord> = {}): SubtitleRecord {
  return {
    id: "subtitle-1",
    line_id: "line-1",
    line_index: 0,
    start_seconds: 1,
    end_seconds: 3,
    image_png_base64: null,
    detection: {
      status: "confirmed",
      start_reason: "test",
      end_reason: "test",
      needs_review: false,
    },
    ocr: {
      status: "completed",
      raw_text: "첫째 줄\n둘째 줄",
      raw_lines: ["첫째 줄", "둘째 줄"],
      confidence: 0.9,
      error: null,
    },
    corrected_ko: null,
    translation: {
      status: "completed",
      source_ko: "첫째 줄\n둘째 줄",
      generated_ja: "1行目\n2行目",
      user_ja: null,
      error: null,
    },
    ...overrides,
  };
}

function project(overrides: Partial<AnalysisProject> = {}): AnalysisProject {
  return {
    schema_version: 2,
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
      start_seconds: 1,
      end_seconds: 5,
      subtitle_region: { x: 0, y: 0.7, width: 1, height: 0.3 },
      sample_interval_ms: 200,
      line_split_ratio: null,
      minimum_display_duration_ms: 1200,
    },
    configuration: { detection: {}, ocr: {}, translation: {} },
    subtitles: [subtitle()],
    relationships: { simultaneous: [] },
    processing: {
      state: "completed",
      sample_count: 20,
      subtitle_count: 1,
      detection_count: 1,
      dropped_intervals: [],
      ocr_call_count: 1,
      translation_call_count: 1,
      errors: [],
    },
    ...overrides,
  };
}

describe("playback subtitle panel", () => {
  it("shows effective Korean and Japanese with their line breaks", () => {
    render(<PlaybackSubtitlePanel project={project()} currentTime={2} />);
    const korean = screen.getByText("첫째 줄 둘째 줄");
    const japanese = screen.getByText("1行目 2行目");
    expect(korean.textContent).toBe("첫째 줄\n둘째 줄");
    expect(japanese.textContent).toBe("1行目\n2行目");
  });

  it("shows corrections and does not present a stale generated translation", () => {
    const corrected = subtitle({
      corrected_ko: "수정한 원문",
      translation: {
        status: "stale",
        source_ko: "첫째 줄\n둘째 줄",
        generated_ja: "古い自動訳",
        user_ja: null,
        error: null,
      },
    });
    render(
      <PlaybackSubtitlePanel
        project={project({ subtitles: [corrected] })}
        currentTime={2}
      />,
    );
    expect(screen.getByText("수정한 원문")).toBeInTheDocument();
    expect(screen.getByText("原文修正済み")).toBeInTheDocument();
    expect(screen.getByText(/再翻訳が必要/)).toBeInTheDocument();
    expect(screen.queryByText("古い自動訳")).not.toBeInTheDocument();
  });

  it("keeps a user Japanese edit visible and marks it", () => {
    const edited = subtitle({
      translation: {
        status: "stale",
        source_ko: "첫째 줄\n둘째 줄",
        generated_ja: "古い自動訳",
        user_ja: "ユーザーが確認した訳",
        error: null,
      },
    });
    render(
      <PlaybackSubtitlePanel
        project={project({ subtitles: [edited] })}
        currentTime={2}
      />,
    );
    expect(screen.getByText("ユーザーが確認した訳")).toBeInTheDocument();
    expect(screen.getByText("ユーザー修正")).toBeInTheDocument();
    expect(screen.queryByText("古い自動訳")).not.toBeInTheDocument();
  });

  it.each([
    ["pending", null, "日本語訳を準備中です。"],
    ["error", "Ollama unavailable", "日本語訳がありません。"],
  ] as const)("shows the %s translation state", (status, error, message) => {
    const item = subtitle({
      translation: {
        status,
        source_ko: "원문",
        generated_ja: null,
        user_ja: null,
        error,
      },
    });
    render(
      <PlaybackSubtitlePanel
        project={project({ subtitles: [item] })}
        currentTime={2}
      />,
    );
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByText(/Ollama unavailable/)).not.toBeInTheDocument();
  });

  it("distinguishes unanalyzed, out-of-range, and empty states without exposing OCR errors", () => {
    const rendered = render(
      <PlaybackSubtitlePanel project={null} currentTime={2} />,
    );
    expect(screen.getByText(/解析結果はまだありません/)).toBeInTheDocument();

    rendered.rerender(
      <PlaybackSubtitlePanel project={project()} currentTime={0.5} />,
    );
    expect(screen.getByText("この時刻は解析範囲外です。")).toBeInTheDocument();

    rendered.rerender(
      <PlaybackSubtitlePanel
        project={project({ subtitles: [] })}
        currentTime={2}
      />,
    );
    expect(
      screen.getByText("この時刻に字幕はありません。"),
    ).toBeInTheDocument();

    rendered.rerender(
      <PlaybackSubtitlePanel
        project={project({
          subtitles: [
            subtitle({
              ocr: {
                ...subtitle().ocr,
                raw_text: "",
                raw_lines: [],
                error: "画像を認識できませんでした",
              },
              translation: {
                status: "error",
                source_ko: "",
                generated_ja: null,
                user_ja: null,
                error: "翻訳対象がありません",
              },
            }),
          ],
          processing: {
            state: "completed_with_errors",
            sample_count: 20,
            subtitle_count: 0,
            detection_count: 1,
            dropped_intervals: [],
            ocr_call_count: 1,
            translation_call_count: 0,
            errors: [
              {
                phase: "ocr",
                timestamp_seconds: 2,
                message: "画像を認識できませんでした",
              },
            ],
          },
        })}
        currentTime={2.1}
      />,
    );
    expect(
      screen.getByText("この時刻に字幕はありません。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("韓国語原文がありません。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/画像を認識できませんでした/),
    ).not.toBeInTheDocument();
  });

  it("renders simultaneous subtitle records in line order", () => {
    const lower = Object.assign(
      subtitle({ id: "lower", ocr: { ...subtitle().ocr, raw_text: "아래" } }),
      { line_index: 1 },
    );
    const upper = Object.assign(
      subtitle({ id: "upper", ocr: { ...subtitle().ocr, raw_text: "위" } }),
      { line_index: 0 },
    );
    render(
      <PlaybackSubtitlePanel
        project={project({ subtitles: [lower, upper] })}
        currentTime={2}
      />,
    );
    const items = screen.getAllByRole("article");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("위");
    expect(items[1]).toHaveTextContent("아래");
  });
});
