import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import type { AnalysisProject } from "./analysisProject";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ save: vi.fn() }));

const region = { x: 0.1, y: 0.7, width: 0.8, height: 0.2 };

function project(): AnalysisProject {
  return {
    schema_version: 2,
    kind: "lol-translator-project",
    source_video: {
      path: "C:/video.mp4",
      name: "video.mp4",
      size_bytes: 1,
      modified_unix_ms: 1,
      sha256: "abc",
      duration_seconds: 1_000,
      width: 1920,
      height: 1080,
    },
    analysis: {
      mode: "range",
      start_seconds: 60,
      end_seconds: 180,
      subtitle_region: region,
      sample_interval_ms: 200,
      line_split_ratio: null,
      minimum_display_duration_ms: 1200,
    },
    configuration: { detection: {}, ocr: {}, translation: {} },
    subtitles: [
      {
        id: "subtitle-00001",
        line_id: "line-1",
        line_index: 0,
        start_seconds: 60,
        end_seconds: 61,
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
      },
    ],
    relationships: { simultaneous: [] },
    processing: {
      state: "completed",
      sample_count: 600,
      subtitle_count: 1,
      detection_count: 1,
      dropped_intervals: [],
      ocr_call_count: 1,
      translation_call_count: 1,
      errors: [],
    },
  };
}

function Harness({
  initial = null,
  duration = 1_000,
}: {
  initial?: AnalysisProject | null;
  duration?: number;
}) {
  const [value, setValue] = useState(initial);
  return (
    <AnalysisWorkspace
      path="C:/video.mp4"
      region={region}
      duration={duration}
      currentTime={75}
      project={value}
      setProject={setValue}
      onSeek={() => {}}
    />
  );
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it("accepts a range longer than three minutes without adding an upper limit", async () => {
  vi.mocked(invoke).mockImplementation((command) => {
    if (command === "start_analysis")
      return Promise.resolve({ job_id: "job-1" });
    return new Promise(() => {});
  });
  render(<Harness />);
  fireEvent.change(screen.getByRole("slider", { name: "解析終了" }), {
    target: { value: "960" },
  });
  fireEvent.change(screen.getByRole("slider", { name: "解析開始" }), {
    target: { value: "60" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解析を開始" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("start_analysis", {
      request: {
        schema_version: 2,
        video_path: "C:/video.mp4",
        subtitle_region: region,
        analysis_range: {
          mode: "range",
          start_seconds: 60,
          end_seconds: 960,
        },
        settings: {
          sample_interval_ms: 200,
          line_split_ratio: null,
          minimum_display_duration_ms: 1200,
        },
      },
    }),
  );
});

it("keeps the range thumbs and numeric time fields in sync", () => {
  render(<Harness />);
  fireEvent.change(screen.getByRole("slider", { name: "解析開始" }), {
    target: { value: "12.3" },
  });
  fireEvent.change(screen.getByRole("slider", { name: "解析終了" }), {
    target: { value: "900.4" },
  });
  expect(screen.getByLabelText("開始（秒）")).toHaveValue(12.3);
  expect(screen.getByLabelText("終了（秒）")).toHaveValue(900.4);

  fireEvent.change(screen.getByLabelText("開始（秒）"), {
    target: { value: "20.5" },
  });
  fireEvent.change(screen.getByLabelText("終了（秒）"), {
    target: { value: "800.6" },
  });
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("20.5");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("800.6");
});

it("blocks analysis for empty, crossed, and out-of-bounds numeric ranges", () => {
  render(<Harness />);
  const start = screen.getByLabelText("開始（秒）");
  const end = screen.getByLabelText("終了（秒）");
  const analyze = screen.getByRole("button", { name: "解析を開始" });

  fireEvent.change(start, { target: { value: "" } });
  expect(analyze).toBeDisabled();
  expect(screen.getByText(/終了時刻は開始より後/)).toBeInTheDocument();

  fireEvent.change(start, { target: { value: "100" } });
  fireEvent.change(end, { target: { value: "100" } });
  expect(analyze).toBeDisabled();

  fireEvent.change(end, { target: { value: "1000.1" } });
  expect(analyze).toBeDisabled();

  fireEvent.change(start, { target: { value: "0" } });
  fireEvent.change(end, { target: { value: "1000" } });
  expect(analyze).toBeEnabled();
});

it("shows the full interval without losing the previous partial range", () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText("開始（秒）"), {
    target: { value: "20" },
  });
  fireEvent.change(screen.getByLabelText("終了（秒）"), {
    target: { value: "80" },
  });

  fireEvent.click(screen.getByLabelText("動画全体を解析"));
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("0");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("1000");
  expect(screen.getByLabelText("開始（秒）")).toBeDisabled();
  expect(screen.getByLabelText("終了（秒）")).toBeDisabled();

  fireEvent.click(screen.getByLabelText("動画全体を解析"));
  expect(screen.getByLabelText("開始（秒）")).toHaveValue(20);
  expect(screen.getByLabelText("終了（秒）")).toHaveValue(80);
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("20");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("80");
});

it("disables range operations before video metadata is available", () => {
  render(<Harness duration={0} />);
  expect(screen.getByRole("slider", { name: "解析開始" })).toBeDisabled();
  expect(screen.getByRole("slider", { name: "解析終了" })).toBeDisabled();
  expect(screen.getByLabelText("開始（秒）")).toBeDisabled();
  expect(screen.getByRole("button", { name: "解析を開始" })).toBeDisabled();
});

it("keeps hundredth-second precision for a very short video", () => {
  render(<Harness duration={0.05} />);
  expect(screen.getByLabelText("開始（秒）")).toHaveAttribute("step", "0.01");
  expect(screen.getByLabelText("終了（秒）")).toHaveValue(0.05);
  expect(screen.getByRole("button", { name: "解析を開始" })).toBeEnabled();
});

it("disables settings while the analyzer start request is pending", async () => {
  vi.mocked(invoke).mockImplementation(() => new Promise(() => {}));
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "解析を開始" }));
  await waitFor(() =>
    expect(screen.getByRole("slider", { name: "解析開始" })).toBeDisabled(),
  );
  expect(screen.getByLabelText("開始（秒）")).toBeDisabled();
  expect(screen.getByLabelText("動画全体を解析")).toBeDisabled();
});

it("allows zero seconds as the minimum display duration", async () => {
  vi.mocked(invoke).mockImplementation((command) => {
    if (command === "start_analysis")
      return Promise.resolve({ job_id: "job-zero-minimum" });
    return new Promise(() => {});
  });
  render(<Harness />);
  fireEvent.change(screen.getByLabelText("最小表示時間（秒）"), {
    target: { value: "0" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解析を開始" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith(
      "start_analysis",
      expect.objectContaining({
        request: expect.objectContaining({
          settings: expect.objectContaining({
            minimum_display_duration_ms: 0,
          }),
        }),
      }),
    ),
  );
});

it("forwards cancellation to the active analyzer job", async () => {
  vi.mocked(invoke).mockImplementation((command) => {
    if (command === "start_analysis")
      return Promise.resolve({ job_id: "job-cancel" });
    if (command === "cancel_analysis_job") return Promise.resolve(undefined);
    return new Promise(() => {});
  });
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "解析を開始" }));
  fireEvent.click(await screen.findByRole("button", { name: "キャンセル" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("cancel_analysis_job", {
      jobId: "job-cancel",
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "解析をキャンセルしました",
  );
});

it("sends null timestamps when whole-video analysis is selected", async () => {
  vi.mocked(invoke).mockImplementation((command) => {
    if (command === "start_analysis")
      return Promise.resolve({ job_id: "job-2" });
    return new Promise(() => {});
  });
  render(<Harness />);
  fireEvent.click(screen.getByLabelText("動画全体を解析"));
  fireEvent.click(screen.getByRole("button", { name: "解析を開始" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith(
      "start_analysis",
      expect.objectContaining({
        request: expect.objectContaining({
          analysis_range: {
            mode: "whole",
            start_seconds: null,
            end_seconds: null,
          },
        }),
      }),
    ),
  );
});

it("restores the saved analysis range and manual line split", () => {
  const value = project();
  value.analysis.line_split_ratio = 0.42;
  render(<Harness initial={value} />);
  expect(screen.getByLabelText("開始（秒）")).toHaveValue(60);
  expect(screen.getByLabelText("終了（秒）")).toHaveValue(180);
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("60");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("180");
  expect(
    screen.getByLabelText("1つのROIを上下2領域としてOCRする"),
  ).toBeChecked();
  expect(screen.getByLabelText("上段の高さ：42%")).toHaveValue("42");
});

it("restores the saved partial range after leaving saved whole-video mode", () => {
  const value = project();
  value.analysis.mode = "whole";
  render(<Harness initial={value} />);
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("0");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("1000");

  fireEvent.click(screen.getByLabelText("動画全体を解析"));
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("60");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("180");
});

it("shows every raw OCR variant retained by timeline stabilization", () => {
  const value = project();
  value.subtitles[0].ocr.variants = [
    {
      start_seconds: 60,
      end_seconds: 60.2,
      raw_text: "원문",
      raw_lines: ["원문"],
      confidence: 0.7,
    },
    {
      start_seconds: 60.2,
      end_seconds: 60.4,
      raw_text: "윈문",
      raw_lines: ["윈문"],
      confidence: 0.9,
    },
  ];
  render(<Harness initial={value} />);
  expect(screen.getByText("統合したOCR候補（2件）")).toBeInTheDocument();
  expect(screen.getByText("윈문")).toBeInTheDocument();
});

it("keeps raw OCR immutable and sends corrected Korean for selected retranslation", async () => {
  vi.mocked(invoke).mockImplementation((command) => {
    if (command === "start_retranslation")
      return Promise.resolve({ job_id: "job-3" });
    return new Promise(() => {});
  });
  render(<Harness initial={project()} />);
  fireEvent.change(screen.getByLabelText("修正後の韓国語"), {
    target: { value: "수정문" },
  });
  fireEvent.change(screen.getByLabelText("ユーザー修正の日本語"), {
    target: { value: "ユーザー訳" },
  });
  expect(
    screen.getByText("원문", { selector: ".raw-output" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("翻訳入力：수정문 / 状態：stale"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "この字幕を再翻訳" }));
  await waitFor(() => {
    const call = vi
      .mocked(invoke)
      .mock.calls.find(([command]) => command === "start_retranslation");
    expect(call).toBeDefined();
    const sent = (call?.[1] as { project: AnalysisProject } | undefined)
      ?.project.subtitles[0];
    expect(sent).toBeDefined();
    if (!sent) throw new Error("missing retranslation request");
    expect(sent.ocr.raw_text).toBe("원문");
    expect(sent.corrected_ko).toBe("수정문");
    expect(sent.translation.user_ja).toBe("ユーザー訳");
  });
});

it("saves the current UTF-8 project through the atomic backend command", async () => {
  vi.mocked(save).mockResolvedValue("C:/saved/結果.json");
  vi.mocked(invoke).mockResolvedValue(undefined);
  const value = project();
  render(<Harness initial={value} />);
  fireEvent.click(screen.getByRole("button", { name: "JSONを保存" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("save_analysis_project", {
      path: "C:/saved/結果.json",
      project: value,
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent("結果.json");
});
