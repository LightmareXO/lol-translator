import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AnalysisRangeSlider } from "./AnalysisRangeSlider";
import { formatRangeTime } from "./analysisRange";

function Harness({
  duration = 1_000,
  initialStart = 60,
  initialEnd = 180,
  wholeVideo = false,
}: {
  duration?: number;
  initialStart?: number;
  initialEnd?: number;
  wholeVideo?: boolean;
}) {
  const [range, setRange] = useState([initialStart, initialEnd]);
  return (
    <AnalysisRangeSlider
      duration={duration}
      currentSeconds={75}
      startSeconds={range[0]}
      endSeconds={range[1]}
      wholeVideo={wholeVideo}
      onChange={(start, end) => setRange([start, end])}
    />
  );
}

afterEach(cleanup);

it("formats analysis times with hours only when needed", () => {
  expect(formatRangeTime(0)).toBe("0:00.0");
  expect(formatRangeTime(65.25)).toBe("1:05.25");
  expect(formatRangeTime(3_661.2)).toBe("1:01:01.2");
});

it("renders two labelled thumbs on a single range control", () => {
  const { container } = render(<Harness />);
  expect(container.querySelectorAll(".analysis-range-control")).toHaveLength(1);
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("60");
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("180");
  expect(screen.getByText("1:00.0")).toBeInTheDocument();
  expect(screen.getByText("3:00.0")).toBeInTheDocument();
  expect(screen.getByText("1:15.0")).toBeInTheDocument();
  expect(container.querySelector(".analysis-range-playhead")).toHaveStyle({
    left: "7.5%",
  });
});

it("prevents either thumb from crossing the other", () => {
  render(<Harness initialStart={100} initialEnd={100.2} />);
  fireEvent.change(screen.getByRole("slider", { name: "解析開始" }), {
    target: { value: "200" },
  });
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveValue("100.1");

  fireEvent.change(screen.getByRole("slider", { name: "解析終了" }), {
    target: { value: "50" },
  });
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("100.2");
});

it("supports direction, Home, and End keys on both thumbs", () => {
  render(<Harness />);
  const start = screen.getByRole("slider", { name: "解析開始" });
  const end = screen.getByRole("slider", { name: "解析終了" });

  fireEvent.keyDown(start, { key: "ArrowRight" });
  expect(start).toHaveValue("60.1");
  fireEvent.keyDown(start, { key: "Home" });
  expect(start).toHaveValue("0");
  fireEvent.keyDown(end, { key: "End" });
  expect(end).toHaveValue("1000");
  fireEvent.keyDown(end, { key: "ArrowLeft" });
  expect(end).toHaveValue("999.9");
});

it("keeps a usable interval for a video shorter than one second", () => {
  render(<Harness duration={0.05} initialStart={0} initialEnd={0.05} />);
  expect(screen.getByRole("slider", { name: "解析開始" })).toHaveAttribute(
    "step",
    "0.01",
  );
  expect(screen.getByRole("slider", { name: "解析終了" })).toHaveValue("0.05");
  expect(screen.getAllByText("0:00.05")).toHaveLength(2);
});

it("shows and disables the full interval in whole-video mode", () => {
  render(<Harness wholeVideo />);
  expect(screen.getByRole("slider", { name: "解析開始" })).toBeDisabled();
  expect(screen.getByRole("slider", { name: "解析終了" })).toBeDisabled();
  expect(screen.getByText(/動画全体（0:00\.0〜16:40\.0）/)).toBeInTheDocument();
});

it("reports changes through the existing seconds contract", () => {
  const onChange = vi.fn();
  render(
    <AnalysisRangeSlider
      duration={900}
      currentSeconds={0}
      startSeconds={0}
      endSeconds={60}
      onChange={onChange}
    />,
  );
  fireEvent.change(screen.getByRole("slider", { name: "解析終了" }), {
    target: { value: "750.5" },
  });
  expect(onChange).toHaveBeenCalledWith(0, 750.5);
});
