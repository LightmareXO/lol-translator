import { invoke } from "@tauri-apps/api/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalysisProject } from "./analysisProject";
import { getVideoContentRect } from "./subtitleRegion";
import { VideoPlayer } from "./VideoPlayer";

let viewport = { x: 100, y: 200, width: 800, height: 600 };
let notifyResize = () => {};
const onChange = vi.fn();
const onBusyChange = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

beforeEach(() => {
  viewport = { x: 100, y: 200, width: 800, height: 600 };
  onChange.mockReset();
  onBusyChange.mockReset();
  vi.mocked(invoke).mockReset();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        notifyResize = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      constructor(type: string, options: PointerEventInit = {}) {
        super(type, options);
        this.pointerId = options.pointerId ?? 1;
      }
    },
  );
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      if (this.classList.contains("region-surface")) {
        const image = getVideoContentRect(viewport, {
          width: 1920,
          height: 1080,
        });
        if (!image) throw new Error("Missing geometry");
        return DOMRect.fromRect({
          x: viewport.x + image.x,
          y: viewport.y + image.y,
          width: image.width,
          height: image.height,
        });
      }
      return DOMRect.fromRect(viewport);
    },
  );
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function loadVideo() {
  const video = screen.getByLabelText("動画：test.mp4") as HTMLVideoElement;
  Object.defineProperties(video, {
    videoWidth: { configurable: true, value: 1920 },
    videoHeight: { configurable: true, value: 1080 },
    duration: { configurable: true, value: 120 },
  });
  fireEvent.loadedMetadata(video);
  fireEvent.loadedData(video);
  return video;
}

function setup() {
  const rendered = render(
    <VideoPlayer
      key="first"
      path="C:/test.mp4"
      url="asset://first"
      onRegionChange={onChange}
      onBusyChange={onBusyChange}
    />,
  );
  const video = loadVideo();
  fireEvent.click(screen.getByRole("button", { name: "字幕範囲を指定" }));
  return {
    ...rendered,
    video,
    surface: screen.getByRole("button", { name: "字幕範囲をドラッグして指定" }),
  };
}

function savedProject(): AnalysisProject {
  return {
    schema_version: 2,
    kind: "lol-translator-project",
    source_video: {
      path: "C:/test.mp4",
      name: "test.mp4",
      size_bytes: 1,
      modified_unix_ms: 1,
      sha256: "abc",
      duration_seconds: 120,
      width: 1920,
      height: 1080,
    },
    analysis: {
      mode: "range",
      start_seconds: 1,
      end_seconds: 4,
      subtitle_region: { x: 0.1, y: 0.7, width: 0.8, height: 0.2 },
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
        start_seconds: 1,
        end_seconds: 2,
        image_png_base64: null,
        detection: {
          status: "confirmed",
          start_reason: "test",
          end_reason: "test",
          needs_review: false,
        },
        ocr: {
          status: "completed",
          raw_text: "안녕",
          raw_lines: ["안녕"],
          confidence: 0.9,
          error: null,
        },
        corrected_ko: null,
        translation: {
          status: "completed",
          source_ko: "안녕",
          generated_ja: "こんにちは",
          user_ja: null,
          error: null,
        },
      },
    ],
    relationships: { simultaneous: [] },
    processing: {
      state: "completed",
      sample_count: 15,
      subtitle_count: 1,
      detection_count: 1,
      dropped_intervals: [],
      ocr_call_count: 1,
      translation_call_count: 1,
      errors: [],
    },
  };
}

function drag(
  surface: HTMLElement,
  start: [number, number],
  end: [number, number],
) {
  fireEvent.pointerDown(surface, {
    pointerId: 1,
    button: 0,
    clientX: start[0],
    clientY: start[1],
  });
  fireEvent.pointerMove(surface, {
    pointerId: 1,
    clientX: end[0],
    clientY: end[1],
  });
  fireEvent.pointerUp(surface, {
    pointerId: 1,
    clientX: end[0],
    clientY: end[1],
  });
}

describe("subtitle selection and playback integration", () => {
  it("shows subtitles by video time and clears them at the end boundary", () => {
    render(
      <VideoPlayer
        path="C:/test.mp4"
        url="asset://first"
        initialProject={savedProject()}
      />,
    );
    const video = loadVideo();
    video.currentTime = 1.5;
    fireEvent.timeUpdate(video);
    const panel = screen.getByRole("region", { name: "再生位置の字幕" });
    expect(panel).toHaveTextContent("안녕");
    expect(panel).toHaveTextContent("こんにちは");
    expect(panel.previousElementSibling).toHaveClass("video-stage");
    expect(video.parentElement).not.toHaveTextContent("こんにちは");
    video.currentTime = 2;
    fireEvent.seeked(video);
    expect(panel).toHaveTextContent("この時刻に字幕はありません。");
    expect(within(panel).queryByText("こんにちは")).not.toBeInTheDocument();
  });

  it("shows overlapping upper and lower line subtitles together", () => {
    const value = savedProject();
    value.subtitles.push({
      ...value.subtitles[0],
      id: "subtitle-00002",
      line_id: "line-2",
      line_index: 1,
      start_seconds: 1.2,
      end_seconds: 2.5,
      translation: {
        ...value.subtitles[0].translation,
        generated_ja: "下段の訳",
      },
    });
    render(
      <VideoPlayer
        path="C:/test.mp4"
        url="asset://first"
        initialProject={value}
      />,
    );
    const video = loadVideo();
    video.currentTime = 1.5;
    fireEvent.timeUpdate(video);
    const panel = screen.getByRole("region", { name: "再生位置の字幕" });
    expect(within(panel).getByText("こんにちは")).toBeInTheDocument();
    expect(within(panel).getByText("下段の訳")).toBeInTheDocument();
  });

  it("updates the playback panel when Korean and Japanese are edited", () => {
    render(
      <VideoPlayer
        path="C:/test.mp4"
        url="asset://first"
        initialProject={savedProject()}
      />,
    );
    const video = loadVideo();
    video.currentTime = 1.5;
    fireEvent.timeUpdate(video);
    const panel = screen.getByRole("region", { name: "再生位置の字幕" });

    fireEvent.change(screen.getByLabelText("修正後の韓国語"), {
      target: { value: "수정한 안녕" },
    });
    expect(panel).toHaveTextContent("수정한 안녕");
    expect(panel).toHaveTextContent("再翻訳が必要です");
    expect(within(panel).queryByText("こんにちは")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("ユーザー修正の日本語"), {
      target: { value: "ユーザー修正訳" },
    });
    expect(panel).toHaveTextContent("ユーザー修正訳");
    expect(panel).toHaveTextContent("ユーザー修正");
  });

  it("passes the selected coordinates and time range to the analysis job", async () => {
    vi.mocked(invoke).mockImplementation((command) => {
      if (command === "start_analysis")
        return Promise.resolve({ job_id: "job-1" });
      return new Promise(() => {});
    });
    const { surface } = setup();
    const submit = () => screen.getByRole("button", { name: "解析を開始" });
    expect(submit()).toBeDisabled();
    drag(surface, [300, 500], [700, 725]);
    expect(submit()).toBeEnabled();
    fireEvent.click(submit());
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("start_analysis", {
        request: {
          schema_version: 2,
          video_path: "C:/test.mp4",
          subtitle_region: { x: 0.25, y: 0.5, width: 0.5, height: 0.5 },
          analysis_range: {
            mode: "range",
            start_seconds: 0,
            end_seconds: 60,
          },
          settings: {
            sample_interval_ms: 200,
            line_split_ratio: null,
            minimum_display_duration_ms: 1200,
          },
        },
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "字幕範囲を指定" }),
      ).toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "範囲をクリア" })).toBeDisabled();
    expect(screen.getByText("字幕範囲を選択済み")).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
  });

  it("visualizes the manual two-line OCR split inside the selected ROI", async () => {
    const { surface } = setup();
    drag(surface, [300, 500], [700, 725]);
    fireEvent.click(screen.getByLabelText("1つのROIを上下2領域としてOCRする"));

    const separator = await screen.findByRole("separator", {
      name: "上下段のOCR分割位置",
    });
    expect(separator).toHaveStyle({ top: "50%" });
    expect(screen.getByText("上段OCR")).toBeInTheDocument();
    expect(screen.getByText("下段OCR")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("上段の高さ：50%"), {
      target: { value: "42" },
    });
    await waitFor(() => expect(separator).toHaveStyle({ top: "42%" }));
  });

  it("waits for video dimensions and preserves playback controls outside selection mode", () => {
    render(<VideoPlayer path="C:/test.mp4" url="asset://first" />);
    expect(
      screen.getByRole("button", { name: "字幕範囲を指定" }),
    ).toBeDisabled();
    const video = loadVideo();
    expect(video.controls).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "字幕範囲を指定" }));
    expect(video.pause).toHaveBeenCalled();
    expect(video.controls).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "範囲指定を終了" }));
    expect(video.controls).toBe(true);
    expect(
      screen.queryByRole("button", { name: "字幕範囲をドラッグして指定" }),
    ).not.toBeInTheDocument();
  });

  it("draws a preview then commits normalized coordinates without letterboxing", () => {
    const { surface } = setup();
    expect(surface.parentElement).toHaveStyle({
      left: "0px",
      top: "75px",
      width: "800px",
      height: "450px",
    });
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      button: 0,
      clientX: 300,
      clientY: 500,
    });
    fireEvent.pointerMove(surface, {
      pointerId: 1,
      clientX: 700,
      clientY: 725,
    });
    expect(screen.getByRole("img", { name: "指定中の字幕範囲" })).toHaveStyle({
      left: "25%",
      top: "50%",
      width: "50%",
      height: "50%",
    });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 700, clientY: 725 });
    expect(onChange).toHaveBeenLastCalledWith({
      x: 0.25,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(
      screen.getByRole("img", { name: "選択済みの字幕範囲" }),
    ).toBeInTheDocument();
  });

  it("replaces the previous rectangle and supports reverse drags", () => {
    const { surface } = setup();
    drag(surface, [100, 275], [900, 725]);
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      button: 0,
      clientX: 700,
      clientY: 725,
    });
    fireEvent.pointerMove(surface, {
      pointerId: 1,
      clientX: 300,
      clientY: 500,
    });
    expect(
      screen.queryByRole("img", { name: "選択済みの字幕範囲" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(1);
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 300, clientY: 500 });
    expect(onChange).toHaveBeenLastCalledWith({
      x: 0.25,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });

  it("captures the pointer and clamps a drag that leaves the image", () => {
    const { surface } = setup();
    drag(surface, [500, 500], [2000, -200]);
    expect(surface.setPointerCapture).toHaveBeenCalledWith(1);
    expect(surface.releasePointerCapture).toHaveBeenCalledWith(1);
    expect(onChange).toHaveBeenLastCalledWith({
      x: 0.5,
      y: 0,
      width: 0.5,
      height: 0.5,
    });
  });

  it("preserves the committed selection when a new drag is cancelled or has no area", () => {
    const { surface } = setup();
    drag(surface, [300, 500], [700, 725]);
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      button: 0,
      clientX: 100,
      clientY: 275,
    });
    fireEvent.pointerMove(surface, {
      pointerId: 1,
      clientX: 500,
      clientY: 500,
    });
    fireEvent.pointerCancel(surface, { pointerId: 1 });
    expect(screen.getByRole("img", { name: "選択済みの字幕範囲" })).toHaveStyle(
      { left: "25%", width: "50%" },
    );
    drag(surface, [100, 275], [100, 275]);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("ignores a second pointer and right clicks", () => {
    const { surface } = setup();
    fireEvent.pointerDown(surface, {
      pointerId: 2,
      button: 2,
      clientX: 100,
      clientY: 275,
    });
    expect(surface.setPointerCapture).not.toHaveBeenCalled();
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      button: 0,
      clientX: 300,
      clientY: 500,
    });
    fireEvent.pointerDown(surface, {
      pointerId: 2,
      button: 0,
      clientX: 100,
      clientY: 275,
    });
    fireEvent.pointerUp(surface, { pointerId: 2, clientX: 900, clientY: 725 });
    fireEvent.pointerCancel(surface, { pointerId: 2 });
    fireEvent.lostPointerCapture(surface, { pointerId: 2 });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 700, clientY: 725 });
    expect(onChange).toHaveBeenLastCalledWith({
      x: 0.25,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
  });

  it("repositions the saved rectangle when the viewport changes", () => {
    const { surface } = setup();
    drag(surface, [300, 500], [700, 725]);
    viewport = { x: 40, y: 80, width: 1000, height: 300 };
    act(() => notifyResize());
    const overlay = screen.getByRole("button", {
      name: "字幕範囲をドラッグして指定",
    }).parentElement;
    expect(overlay?.style.top).toBe("0px");
    expect(Number.parseFloat(overlay?.style.left ?? "")).toBeCloseTo(233.333);
    expect(screen.getByRole("img", { name: "選択済みの字幕範囲" })).toHaveStyle(
      { left: "25%", top: "50%", width: "50%", height: "50%" },
    );
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("clears the region and resets it when a different video is mounted", () => {
    const { surface, rerender } = setup();
    drag(surface, [300, 500], [700, 725]);
    fireEvent.click(screen.getByRole("button", { name: "範囲をクリア" }));
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    drag(surface, [300, 500], [700, 725]);
    rerender(
      <VideoPlayer
        key="second"
        path="C:/other.mp4"
        url="asset://second"
        onRegionChange={onChange}
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "範囲をクリア" })).toBeDisabled();
  });

  it("supports keyboard selection and Escape cancellation", () => {
    const { surface, video } = setup();
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
    });
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      button: 0,
      clientX: 300,
      clientY: 500,
    });
    fireEvent.keyDown(surface, { key: "Escape" });
    expect(screen.getByRole("img", { name: "選択済みの字幕範囲" })).toHaveStyle(
      { width: "100%" },
    );
    fireEvent.keyDown(surface, { key: "Escape" });
    expect(video.controls).toBe(true);
  });

  it("clears selection and exits selection mode on a media error", () => {
    const { surface, video } = setup();
    drag(surface, [300, 500], [700, 725]);
    fireEvent.error(video);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "字幕範囲を指定" }),
    ).toBeDisabled();
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
