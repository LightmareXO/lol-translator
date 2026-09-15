import { describe, expect, it } from "vitest";
import {
  getVideoContentRect,
  normalizePoint,
  regionFromPoints,
} from "./subtitleRegion";

describe("video content rectangle (object-fit: contain)", () => {
  it("excludes top and bottom letterboxing", () => {
    expect(
      getVideoContentRect(
        { width: 800, height: 600 },
        { width: 1920, height: 1080 },
      ),
    ).toEqual({ x: 0, y: 75, width: 800, height: 450 });
  });
  it("excludes left and right pillarboxing", () => {
    expect(
      getVideoContentRect(
        { width: 800, height: 450 },
        { width: 600, height: 800 },
      ),
    ).toEqual({ x: 231.25, y: 0, width: 337.5, height: 450 });
  });
  it("uses the whole viewport when aspect ratios match", () => {
    expect(
      getVideoContentRect(
        { width: 960, height: 540 },
        { width: 1920, height: 1080 },
      ),
    ).toEqual({ x: 0, y: 0, width: 960, height: 540 });
  });
  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    "rejects unavailable dimensions: %s",
    (value) => {
      expect(
        getVideoContentRect(
          { width: 800, height: 600 },
          { width: value, height: 1080 },
        ),
      ).toBeNull();
      expect(
        getVideoContentRect(
          { width: 800, height: value },
          { width: 1920, height: 1080 },
        ),
      ).toBeNull();
    },
  );
});

describe("normalized subtitle region", () => {
  it("normalizes client coordinates against the image, not the element or page", () => {
    expect(
      normalizePoint(
        { x: 300, y: 365 },
        { x: 100, y: 275, width: 800, height: 450 },
      ),
    ).toEqual({ x: 0.25, y: 0.2 });
  });
  it("handles a forward drag", () => {
    expect(regionFromPoints({ x: 0.25, y: 0.5 }, { x: 0.75, y: 1 })).toEqual({
      x: 0.25,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
  });
  it.each([
    [
      { x: 0.75, y: 1 },
      { x: 0.25, y: 0.5 },
    ],
    [
      { x: 0.25, y: 1 },
      { x: 0.75, y: 0.5 },
    ],
    [
      { x: 0.75, y: 0.5 },
      { x: 0.25, y: 1 },
    ],
  ])("handles a reverse drag from %o to %o", (start, end) => {
    expect(regionFromPoints(start, end)).toEqual({
      x: 0.25,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
  });
  it("clamps all four boundaries", () => {
    const bounds = { x: 100, y: 275, width: 800, height: 450 };
    expect(
      regionFromPoints(
        normalizePoint({ x: -100, y: -100 }, bounds),
        normalizePoint({ x: 2000, y: 2000 }, bounds),
      ),
    ).toEqual({ x: 0, y: 0, width: 1, height: 1 });
  });
  it("does not create a selection from a click or a line", () => {
    expect(regionFromPoints({ x: 0.2, y: 0.3 }, { x: 0.2, y: 0.3 })).toBeNull();
    expect(regionFromPoints({ x: 0.2, y: 0.3 }, { x: 0.2, y: 0.8 })).toBeNull();
    expect(regionFromPoints({ x: 0.2, y: 0.3 }, { x: 0.9, y: 0.3 })).toBeNull();
  });
});
