/**
 * Data-freshness and (estimated) market-status helpers.
 *
 * Pure and framework-free so they unit-test in the node environment. Kept
 * frontend-only: freshness is derived from the latest bar's timestamp, and
 * market-open is estimated from US market hours, so no backend change is needed.
 */
import type { OHLCVBar } from "./market-data-api";

/**
 * Staleness threshold in seconds. Mirrors the backend's P-004 "fresh data" policy
 * (≤ 600 s), so the indicator explains exactly why the agent denies trades on
 * stale data.
 */
export const STALE_THRESHOLD_SECONDS = 600;

export type Freshness = "FRESH" | "STALE" | "UNKNOWN";

/** Parse a backend timestamp, treating a missing timezone marker as UTC. */
export function parseUtc(value: string): number {
  const hasZone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  return Date.parse(hasZone ? value : `${value}Z`);
}

/** The timestamp of the most recent bar, or null when there are none. */
export function latestBarTimestamp(bars: OHLCVBar[] | null | undefined): string | null {
  if (!bars || bars.length === 0) return null;
  return bars[bars.length - 1]?.timestamp ?? null;
}

/** Age of a timestamp in seconds relative to `nowMs`, or null if unparseable. */
export function ageSeconds(timestamp: string | null | undefined, nowMs: number): number | null {
  if (!timestamp) return null;
  const ms = parseUtc(timestamp);
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, Math.floor((nowMs - ms) / 1000));
}

/** Classify an age (seconds) against the staleness threshold. */
export function freshness(ageSec: number | null): Freshness {
  if (ageSec === null) return "UNKNOWN";
  return ageSec <= STALE_THRESHOLD_SECONDS ? "FRESH" : "STALE";
}

/** Human-readable age, e.g. "3s", "12m", "4h", "6d". Em dash when unknown. */
export function formatAge(ageSec: number | null): string {
  if (ageSec === null) return "—";
  if (ageSec < 60) return `${ageSec}s`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m`;
  if (ageSec < 86400) return `${Math.floor(ageSec / 3600)}h`;
  return `${Math.floor(ageSec / 86400)}d`;
}

export type MarketStatus = "OPEN" | "CLOSED";

/**
 * Best-effort estimate of whether the US equities regular session is open at
 * `date`: Mon–Fri, 09:30–16:00 America/New_York. Holidays are not accounted for,
 * so the UI labels this as estimated. Deterministic (uses Intl time-zone parts).
 */
export function estimatedMarketStatus(date: Date): MarketStatus {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);

  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const weekday = get("weekday");
  if (weekday === "Sat" || weekday === "Sun") return "CLOSED";

  let hour = Number(get("hour"));
  if (hour === 24) hour = 0; // some runtimes emit "24" for midnight
  const minute = Number(get("minute"));
  const minutes = hour * 60 + minute;
  const open = 9 * 60 + 30;
  const close = 16 * 60;
  return minutes >= open && minutes < close ? "OPEN" : "CLOSED";
}
