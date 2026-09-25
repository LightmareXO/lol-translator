import { useState } from "react";
import { formatRangeTime } from "./analysisRange";

interface Props {
  duration: number;
  currentSeconds: number;
  startSeconds: number;
  endSeconds: number;
  disabled?: boolean;
  wholeVideo?: boolean;
  onChange: (startSeconds: number, endSeconds: number) => void;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function precisionFor(duration: number): { step: number; digits: number } {
  return duration > 0 && duration < 1
    ? { step: 0.01, digits: 2 }
    : { step: 0.1, digits: 1 };
}

function round(value: number, digits: number): number {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

export function AnalysisRangeSlider({
  duration,
  currentSeconds,
  startSeconds,
  endSeconds,
  disabled = false,
  wholeVideo = false,
  onChange,
}: Props) {
  const [focusedThumb, setFocusedThumb] = useState<"start" | "end" | null>(
    null,
  );
  const hasDuration = Number.isFinite(duration) && duration > 0;
  const { step, digits } = precisionFor(duration);
  const minimumGap = Math.min(step, Math.max(0, duration));
  const controlsDisabled = disabled || wholeVideo || !hasDuration;
  const selectedStart = wholeVideo
    ? 0
    : clamp(
        Number.isFinite(startSeconds) ? startSeconds : 0,
        0,
        Math.max(0, duration - minimumGap),
      );
  const selectedEnd = wholeVideo
    ? Math.max(0, duration)
    : clamp(
        Number.isFinite(endSeconds) ? endSeconds : duration,
        minimumGap,
        Math.max(minimumGap, duration),
      );
  const safeStart = Math.min(selectedStart, selectedEnd - minimumGap);
  const safeEnd = Math.max(selectedEnd, safeStart + minimumGap);
  const startPercent = hasDuration ? (safeStart / duration) * 100 : 0;
  const endPercent = hasDuration ? (safeEnd / duration) * 100 : 100;
  const safeCurrent = Number.isFinite(currentSeconds)
    ? clamp(currentSeconds, 0, Math.max(0, duration))
    : 0;
  const currentPercent = hasDuration ? (safeCurrent / duration) * 100 : 0;

  function changeStart(nextValue: number) {
    const maximum = Math.max(0, safeEnd - minimumGap);
    onChange(round(clamp(nextValue, 0, maximum), digits), safeEnd);
  }

  function changeEnd(nextValue: number) {
    const minimum = Math.min(duration, safeStart + minimumGap);
    onChange(safeStart, round(clamp(nextValue, minimum, duration), digits));
  }

  function handleKeyboard(
    event: React.KeyboardEvent<HTMLInputElement>,
    thumb: "start" | "end",
  ) {
    let next: number | null = null;
    const current = thumb === "start" ? safeStart : safeEnd;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      next = current - step;
    } else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      next = current + step;
    } else if (event.key === "PageDown") {
      next = current - step * 10;
    } else if (event.key === "PageUp") {
      next = current + step * 10;
    } else if (event.key === "Home") {
      next = thumb === "start" ? 0 : safeStart + minimumGap;
    } else if (event.key === "End") {
      next = thumb === "start" ? safeEnd - minimumGap : duration;
    }
    if (next == null) return;
    event.preventDefault();
    if (thumb === "start") changeStart(next);
    else changeEnd(next);
  }

  return (
    <fieldset className="analysis-range" disabled={controlsDisabled}>
      <legend>解析区間</legend>
      <div className="analysis-range-values">
        <label htmlFor="analysis-range-start">
          開始 <strong>{formatRangeTime(safeStart)}</strong>
        </label>
        <span className="analysis-range-current">
          再生 <strong>{formatRangeTime(safeCurrent)}</strong>
        </span>
        <label htmlFor="analysis-range-end">
          終了 <strong>{formatRangeTime(safeEnd)}</strong>
        </label>
      </div>
      <div
        className={`analysis-range-control${focusedThumb ? ` focus-${focusedThumb}` : ""}`}
      >
        <div className="analysis-range-track" aria-hidden="true">
          <span
            className="analysis-range-selection"
            style={{ left: `${startPercent}%`, right: `${100 - endPercent}%` }}
          />
        </div>
        <div className="analysis-range-playhead-track" aria-hidden="true">
          <span
            className="analysis-range-playhead"
            style={{ left: `${currentPercent}%` }}
          />
        </div>
        <input
          id="analysis-range-start"
          className="analysis-range-input analysis-range-start"
          type="range"
          min={0}
          max={Math.max(0, duration)}
          step={step}
          value={safeStart}
          aria-label="解析開始"
          aria-valuetext={formatRangeTime(safeStart)}
          onFocus={() => setFocusedThumb("start")}
          onBlur={() => setFocusedThumb(null)}
          onChange={(event) => changeStart(event.currentTarget.valueAsNumber)}
          onKeyDown={(event) => handleKeyboard(event, "start")}
        />
        <input
          id="analysis-range-end"
          className="analysis-range-input analysis-range-end"
          type="range"
          min={0}
          max={Math.max(0, duration)}
          step={step}
          value={safeEnd}
          aria-label="解析終了"
          aria-valuetext={formatRangeTime(safeEnd)}
          onFocus={() => setFocusedThumb("end")}
          onBlur={() => setFocusedThumb(null)}
          onChange={(event) => changeEnd(event.currentTarget.valueAsNumber)}
          onKeyDown={(event) => handleKeyboard(event, "end")}
        />
      </div>
      <p className="selection-help analysis-range-help">
        {wholeVideo
          ? `動画全体（${formatRangeTime(0)}〜${formatRangeTime(duration)}）を解析します。`
          : "つまみはマウス、方向キー、Home・Endキーで調整できます。"}
      </p>
    </fieldset>
  );
}
