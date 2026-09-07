import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

// Browser APIs are installed only by tests that need them, never suite-wide.
afterEach(() => { vi.unstubAllGlobals(); });

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", { value: ResizeObserverMock });
