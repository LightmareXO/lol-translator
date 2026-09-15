import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getVideoContentRect } from "./subtitleRegion";
import { VideoPlayer } from "./VideoPlayer";

let viewport = { x: 100, y: 200, width: 800, height: 600 };
let notifyResize = () => {};
const onChange = vi.fn();

beforeEach(() => {
  viewport = { x: 100, y: 200, width: 800, height: 600 };
  onChange.mockReset();
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
  });
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
