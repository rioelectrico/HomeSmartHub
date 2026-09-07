"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

// Only share in-flight reads, never persisted user data or mutation payloads.
const inFlight = new Map<string, Promise<unknown>>();
function read<T>(path: string): Promise<T> {
  let pending = inFlight.get(path);
  if (!pending) {
    pending = apiFetch<T>(path).finally(() => inFlight.delete(path));
    inFlight.set(path, pending);
  }
  return pending as Promise<T>;
}

type ResourceState<T> = { path: string; data?: T; error?: string; updatedAt?: number };

export function useResource<T>(path: string | null, intervalMs = 15000, stopWhen?: (data: T) => boolean) {
  const [state, setState] = useState<ResourceState<T>>();
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const intervalRef = useRef(intervalMs);
  const stopWhenRef = useRef(stopWhen);
  const scheduleRef = useRef<() => void>(() => {});
  const refresh = useCallback(() => refreshRef.current(), []);

  useEffect(() => {
    intervalRef.current = intervalMs;
    scheduleRef.current();
  }, [intervalMs]);

  useEffect(() => { stopWhenRef.current = stopWhen; }, [stopWhen]);

  useEffect(() => {
    if (!path) { refreshRef.current = async () => {}; return; }
    let active = true;
    let requested = false;
    let stopped = false;
    let pending: Promise<void> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function schedule() {
      clearTimeout(timer);
      if (active && !stopped && intervalRef.current > 0 && document.visibilityState === "visible") timer = setTimeout(load, intervalRef.current);
    }

    function load(): Promise<void> {
      if (!active || stopped || document.visibilityState !== "visible") return Promise.resolve();
      if (pending) return pending;
      requested = true;
      clearTimeout(timer);
      pending = read<T>(path!).then((data) => {
        if (stopWhenRef.current?.(data)) stopped = true;
        if (active) setState({ path: path!, data, updatedAt: Date.now() });
      }).catch(() => {
        if (active) setState((previous) => ({ ...(previous?.path === path ? previous : {}), path: path!, error: "No pudimos cargar los datos. Intentá nuevamente." }));
      }).finally(() => { pending = undefined; schedule(); });
      return pending;
    }

    scheduleRef.current = schedule;
    refreshRef.current = async () => {
      // A mutation invalidates any read that started before it completed.
      if (pending) await pending;
      await load();
    };
    function visibilityChanged() {
      clearTimeout(timer);
      if (document.visibilityState === "visible" && (intervalRef.current > 0 || !requested)) void load();
    }
    void load();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => { active = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibilityChanged); };
  }, [path]);

  const current = state?.path === path ? state : undefined;
  return { data: current?.data, error: current?.error, updatedAt: current?.updatedAt, loading: Boolean(path && !current), refresh };
}
