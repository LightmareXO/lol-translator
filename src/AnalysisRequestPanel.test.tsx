import { invoke } from "@tauri-apps/api/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalysisRequestPanel } from "./AnalysisRequestPanel";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
const path = "C:/動画/한국어 [test].mp4";
const region = { x: 0.1, y: 0.7, width: 0.8, height: 0.2 };
const button = () =>
  screen.getByRole("button", { name: "Pythonへ渡して入力を検証" });
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it("requires a region", () => {
  render(<AnalysisRequestPanel path={path} region={null} />);
  expect(button()).toBeDisabled();
  fireEvent.click(button());
  expect(invoke).not.toHaveBeenCalled();
});

it("sends the exact contract and displays the saved path", async () => {
  vi.mocked(invoke).mockResolvedValue("C:/cache/request.json");
  render(<AnalysisRequestPanel path={path} region={region} />);
  fireEvent.click(button());
  expect(invoke).toHaveBeenCalledWith("validate_analysis_request", {
    request: { video_path: path, subtitle_region: region },
  });
  expect(await screen.findByRole("status")).toHaveTextContent(
    "C:/cache/request.json",
  );
});

it("prevents duplicate submissions and allows retry after an error", async () => {
  let reject!: (reason: string) => void;
  vi.mocked(invoke).mockImplementationOnce(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  render(<AnalysisRequestPanel path={path} region={region} />);
  const submit = button();
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(submit).toBeDisabled();
  expect(invoke).toHaveBeenCalledTimes(1);
  await act(async () => reject("Pythonを起動できません"));
  expect(screen.getByRole("alert")).toHaveTextContent("Pythonを起動できません");
  expect(button()).toBeEnabled();
  vi.mocked(invoke).mockResolvedValue("C:/cache/retry.json");
  fireEvent.click(button());
  expect(await screen.findByRole("status")).toHaveTextContent("retry.json");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("does not show a stale result after the input panel is replaced", async () => {
  let resolve!: (path: string) => void;
  vi.mocked(invoke).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { rerender } = render(
    <AnalysisRequestPanel key="old" path={path} region={region} />,
  );
  fireEvent.click(button());
  rerender(<AnalysisRequestPanel key="new" path="C:/new.mp4" region={null} />);
  await act(async () => resolve("C:/cache/old.json"));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(button()).toBeDisabled();
});
