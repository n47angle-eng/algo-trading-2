import "@testing-library/jest-dom/vitest";
import { beforeEach, vi } from "vitest";

import { __resetHttpCacheForTests } from "../lib/httpCache";
import "../styles/global.css";
import "../styles/results.css";
import "../styles/shell.css";

/*
 * The API client caches hot GETs in-memory for 12–30s. Tests share one module
 * instance, so without this reset a response fetched by one test is still
 * cached when the next test stubs a different one — which silently turns a
 * fail-closed assertion into the previous test's happy-path DOM.
 */
beforeEach(() => {
  __resetHttpCacheForTests();
});

// Lightweight Charts (fancy-canvas) needs matchMedia + ResizeObserver in jsdom.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: ResizeObserverStub,
});
