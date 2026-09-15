import { type PointerEvent, useRef, useState } from "react";
import {
  normalizePoint,
  type Point,
  type Rectangle,
  regionFromPoints,
  type SubtitleRegion,
} from "./subtitleRegion";

interface Props {
  bounds: Rectangle;
  editing: boolean;
  region: SubtitleRegion | null;
  onChange: (region: SubtitleRegion) => void;
  onExit: () => void;
}

interface Drag {
  pointerId: number;
  start: Point;
  end: Point;
}

export function SubtitleRegionOverlay({
  bounds,
  editing,
  region,
  onChange,
  onExit,
}: Props) {
  const activeDrag = useRef<Drag | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const displayed = drag ? regionFromPoints(drag.start, drag.end) : region;

  function pointFor(event: PointerEvent<HTMLButtonElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return normalizePoint(
      { x: event.clientX, y: event.clientY },
      { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
    );
  }

  function cancelDrag(surface?: HTMLButtonElement) {
    const pointerId = activeDrag.current?.pointerId;
    activeDrag.current = null;
    setDrag(null);
    if (pointerId !== undefined && surface?.hasPointerCapture(pointerId)) {
      surface.releasePointerCapture(pointerId);
    }
  }

  function startDrag(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || activeDrag.current) return;
    event.preventDefault();
    event.currentTarget.focus();
    const start = pointFor(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    const next = { start, end: start, pointerId: event.pointerId };
    activeDrag.current = next;
    setDrag(next);
  }

  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    if (activeDrag.current?.pointerId !== event.pointerId) return;
    const next = { ...activeDrag.current, end: pointFor(event) };
    activeDrag.current = next;
    setDrag(next);
  }

  function finishDrag(event: PointerEvent<HTMLButtonElement>) {
    if (activeDrag.current?.pointerId !== event.pointerId) return;
    const next = regionFromPoints(activeDrag.current.start, pointFor(event));
    cancelDrag(event.currentTarget);
    if (next) onChange(next);
  }

  return (
    <div
      className="region-overlay"
      style={{
        left: bounds.x,
        top: bounds.y,
        width: bounds.width,
        height: bounds.height,
      }}
    >
      {editing && (
        <button
          type="button"
          className="region-surface"
          aria-label="字幕範囲をドラッグして指定"
          aria-describedby="region-instructions"
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={finishDrag}
          onPointerCancel={(event) => {
            if (activeDrag.current?.pointerId === event.pointerId) cancelDrag();
          }}
          onLostPointerCapture={(event) => {
            if (activeDrag.current?.pointerId === event.pointerId) cancelDrag();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              if (activeDrag.current) cancelDrag(event.currentTarget);
              else onExit();
            } else if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              cancelDrag(event.currentTarget);
              onChange({ x: 0, y: 0, width: 1, height: 1 });
            }
          }}
        />
      )}
      {displayed && (
        <div
          role="img"
          aria-label={drag ? "指定中の字幕範囲" : "選択済みの字幕範囲"}
          className="region-rectangle"
          style={{
            left: `${displayed.x * 100}%`,
            top: `${displayed.y * 100}%`,
            width: `${displayed.width * 100}%`,
            height: `${displayed.height * 100}%`,
          }}
        />
      )}
    </div>
  );
}
