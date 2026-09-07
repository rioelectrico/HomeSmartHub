import { StrictMode } from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useResource } from "@/lib/use-resource";

beforeEach(() => { vi.useFakeTimers(); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("polls only while visible and cleans up when unmounted", async () => {
  let count = 0;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: ++count })));
  const { result, unmount } = renderHook(() => useResource<{ count: number }>("/poll", 1000));
  await act(async () => {});
  expect(result.current.data?.count).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(result.current.data?.count).toBe(2);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(4000); });
  expect(count).toBe(2);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(result.current.data?.count).toBe(3);
  unmount();
  await vi.advanceTimersByTimeAsync(4000);
  expect(count).toBe(3);
});

it("deduplicates StrictMode and slow requests instead of overlapping polls", async () => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  const { result, unmount } = renderHook(() => useResource<{ value: number }>("/slow", 1000), { wrapper: StrictMode });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { resolve(Response.json({ value: 7 })); });
  expect(result.current.data?.value).toBe(7);
  unmount();
});

it("ignores old-home responses after a resource switch", async () => {
  let resolveOld!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input) === "/old" ? new Promise<Response>((done) => { resolveOld = done; }) : Promise.resolve(Response.json({ home: "new" }))));
  const { result, rerender } = renderHook(({ path }) => useResource<{ home: string }>(path), { initialProps: { path: "/old" } });
  rerender({ path: "/new" });
  await act(async () => {});
  expect(result.current.data?.home).toBe("new");
  await act(async () => { resolveOld(Response.json({ home: "old" })); });
  expect(result.current.data?.home).toBe("new");
});

it("pauses automatic updates without fetching on pause or visibility changes", async () => {
  let count = 0;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: ++count })));
  const { result, rerender } = renderHook(({ interval }) => useResource<{ count: number }>("/pausable", interval), { initialProps: { interval: 1000 } });
  await act(async () => {});
  expect(result.current.data?.count).toBe(1);
  rerender({ interval: 0 });
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); document.dispatchEvent(new Event("visibilitychange")); });
  expect(count).toBe(1);
  await act(async () => { await result.current.refresh(); });
  expect(result.current.data?.count).toBe(2);
});

it("keeps prior data with an explicit stale warning after a polling failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ value: 3 })).mockResolvedValue(new Response(null, { status: 500 })));
  const { result } = renderHook(() => useResource<{ value: number }>("/stale", 1000));
  await act(async () => {});
  const updatedAt = result.current.updatedAt;
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(result.current.data?.value).toBe(3);
  expect(result.current.error).toMatch(/no pudimos cargar/i);
  expect(result.current.updatedAt).toBe(updatedAt);
});

it("stops scheduling after a terminal resource response", async () => {
  let count = 0;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: ++count === 1 ? "pending" : "completed" })));
  const { result } = renderHook(() => useResource<{ status: string }>("/command", 1000, (data) => data.status === "completed"));
  await act(async () => {});
  expect(result.current.data?.status).toBe("pending");
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(result.current.data?.status).toBe("completed");
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(fetch).toHaveBeenCalledTimes(2);
});
