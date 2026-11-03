"use client";

import { useEffect, useRef } from "react";

/**
 * Calls `onTick` every `intervalMs` while `enabled` (#143 live refresh). The
 * initial load stays the caller's responsibility; this only drives the repeats.
 * The latest callback is always used, so passing an inline function is fine.
 */
export function usePolling(onTick: () => void, intervalMs: number, enabled = true): void {
  const saved = useRef(onTick);
  useEffect(() => {
    saved.current = onTick;
  }, [onTick]);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;
    const id = setInterval(() => saved.current(), intervalMs);
    return () => clearInterval(id);
  }, [enabled, intervalMs]);
}
