export interface Size {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface Rectangle extends Point, Size {}

/** Coordinates relative to the actual video image, in the range 0..1. */
export type SubtitleRegion = Rectangle;

/** Matches object-fit: contain and object-position: center. */
export function getVideoContentRect(
  viewport: Size,
  video: Size,
): Rectangle | null {
  if (
    ![viewport.width, viewport.height, video.width, video.height].every(
      (value) => Number.isFinite(value) && value > 0,
    )
  )
    return null;

  const scale = Math.min(
    viewport.width / video.width,
    viewport.height / video.height,
  );
  const width = video.width * scale;
  const height = video.height * scale;
  return {
    x: (viewport.width - width) / 2,
    y: (viewport.height - height) / 2,
    width,
    height,
  };
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));

/** The point and bounds must use the same coordinate system (e.g. client pixels). */
export function normalizePoint(point: Point, bounds: Rectangle): Point {
  return {
    x: clamp((point.x - bounds.x) / bounds.width),
    y: clamp((point.y - bounds.y) / bounds.height),
  };
}

/** Accepts normalized points; a click or a zero-area drag does not select a region. */
export function regionFromPoints(
  start: Point,
  end: Point,
): SubtitleRegion | null {
  const x = Math.min(clamp(start.x), clamp(end.x));
  const y = Math.min(clamp(start.y), clamp(end.y));
  const width = Math.max(clamp(start.x), clamp(end.x)) - x;
  const height = Math.max(clamp(start.y), clamp(end.y)) - y;
  return width > 0 && height > 0 ? { x, y, width, height } : null;
}
