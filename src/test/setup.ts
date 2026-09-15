import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom does not perform layout; geometry-specific tests provide measured sizes.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
