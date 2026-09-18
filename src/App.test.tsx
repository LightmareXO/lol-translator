import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({
  convertFileSrc: vi.fn(),
  invoke: vi.fn(),
}));

const firstPath = "C:/動画/한국어 game #1.mp4";
const assetUrl = "http://asset.localhost/C%3A%2Fvideo.mp4";

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(convertFileSrc).mockReturnValue(assetUrl);
});
afterEach(cleanup);

async function selectFirstVideo() {
  vi.mocked(open).mockResolvedValueOnce(firstPath);
  await userEvent.click(screen.getByRole("button", { name: "動画を選択" }));
  return screen.getByLabelText("動画：한국어 game #1.mp4") as HTMLVideoElement;
}

describe("local video selection and playback", () => {
  it("shows the empty state and safely cancels the first selection", async () => {
    vi.mocked(open).mockResolvedValueOnce(null);
    render(<App />);
    expect(screen.getByText("動画が選択されていません")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "動画を選択" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("動画が選択されていません")).toBeInTheDocument();
  });

  it("loads the selected path with native playback controls and no autoplay", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    expect(open).toHaveBeenCalledWith(
      expect.objectContaining({
        multiple: false,
        directory: false,
      }),
    );
    expect(convertFileSrc).toHaveBeenCalledWith(firstPath);
    expect(video).toHaveAttribute("src", assetUrl);
    expect(video.controls).toBe(true);
    expect(video.autoplay).toBe(false);
    expect(screen.getByRole("status")).toHaveTextContent("読み込んでいます");
    fireEvent.loadedData(video);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("preserves the existing player when another selection is cancelled", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    fireEvent.loadedData(video);
    vi.mocked(open).mockResolvedValueOnce(null);
    await userEvent.click(
      screen.getByRole("button", { name: "別の動画を選択" }),
    );
    expect(screen.getByLabelText("動画：한국어 game #1.mp4")).toBe(video);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("reports a dialog failure and allows retry", async () => {
    render(<App />);
    vi.mocked(open).mockRejectedValueOnce(new Error("dialog failed"));
    await userEvent.click(screen.getByRole("button", { name: "動画を選択" }));
    expect(screen.getByRole("alert")).toHaveTextContent("ファイル選択に失敗");
    await selectFirstVideo();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("rejects unsupported extensions without replacing the current video", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    vi.mocked(open).mockResolvedValueOnce("C:/notes.txt");
    await userEvent.click(
      screen.getByRole("button", { name: "別の動画を選択" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "動画ファイルを選択してください",
    );
    expect(screen.getByLabelText("動画：한국어 game #1.mp4")).toBe(video);
  });

  it("shows media failures and reloads even when the same file is selected", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    fireEvent.error(video);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "動画を再生できませんでした",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    vi.mocked(open).mockResolvedValueOnce(firstPath);
    await userEvent.click(
      screen.getByRole("button", { name: "別の動画を選択" }),
    );
    const replacement = screen.getByLabelText("動画：한국어 game #1.mp4");
    expect(replacement).not.toBe(video);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.loadedData(replacement);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("replaces the player and handles Windows paths and uppercase extensions", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    vi.mocked(open).mockResolvedValueOnce("C:\\動画\\second.MP4");
    await userEvent.click(
      screen.getByRole("button", { name: "別の動画を選択" }),
    );
    expect(screen.getByLabelText("動画：second.MP4")).not.toBe(video);
    expect(video).not.toBeInTheDocument();
    expect(screen.getByText("second.MP4")).toBeInTheDocument();
  });

  it("reports a playback failure after loading has succeeded", async () => {
    render(<App />);
    const video = await selectFirstVideo();
    fireEvent.loadedData(video);
    fireEvent.error(video);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "動画を再生できませんでした",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("ignores events from the old video after replacement", async () => {
    render(<App />);
    const previous = await selectFirstVideo();
    vi.mocked(open).mockResolvedValueOnce("C:/second.mp4");
    vi.mocked(convertFileSrc).mockReturnValueOnce(
      "http://asset.localhost/second.mp4",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "別の動画を選択" }),
    );
    const current = screen.getByLabelText("動画：second.mp4");
    expect(current).toHaveAttribute("src", "http://asset.localhost/second.mp4");
    fireEvent.loadedData(previous);
    expect(screen.getByRole("status")).toBeInTheDocument();
    fireEvent.loadedData(current);
    fireEvent.error(previous);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("prevents opening multiple dialogs while selection is pending", async () => {
    let cancel: (value: null) => void = () => {};
    vi.mocked(open).mockReturnValueOnce(
      new Promise<null>((resolve) => {
        cancel = resolve;
      }),
    );
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "動画を選択" }));
    expect(screen.getByRole("button", { name: "選択中…" })).toBeDisabled();
    await act(async () => cancel(null));
    expect(screen.getByRole("button", { name: "動画を選択" })).toBeEnabled();
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("loads a saved project and its original video", async () => {
    vi.mocked(open).mockResolvedValueOnce("C:/saved/result.json");
    vi.mocked(invoke).mockResolvedValueOnce({
      schema_version: 1,
      kind: "lol-translator-project",
      source_video: {
        path: firstPath,
        name: "한국어 game #1.mp4",
        size_bytes: 1,
        modified_unix_ms: 1,
        sha256: "abc",
        duration_seconds: 120,
        width: 1920,
        height: 1080,
      },
      analysis: {
        mode: "range",
        start_seconds: 0,
        end_seconds: 60,
        subtitle_region: { x: 0.1, y: 0.7, width: 0.8, height: 0.2 },
        sample_interval_ms: 200,
        line_split_ratio: null,
      },
      configuration: { ocr: {}, translation: {} },
      subtitles: [],
      processing: {
        state: "completed",
        sample_count: 0,
        subtitle_count: 0,
        errors: [],
      },
    });
    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "保存済みJSONを開く" }),
    );
    expect(invoke).toHaveBeenCalledWith("load_analysis_project", {
      path: "C:/saved/result.json",
    });
    expect(convertFileSrc).toHaveBeenCalledWith(firstPath);
    expect(
      screen.getByLabelText("動画：한국어 game #1.mp4"),
    ).toBeInTheDocument();
    expect(screen.getByText("字幕範囲を選択済み")).toBeInTheDocument();
  });
});
