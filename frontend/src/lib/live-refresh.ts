/**
 * Live-refresh helpers for Dashboard and Positions (#164). After the first load, a
 * background refresh swaps the values in place; a part whose request fails keeps its
 * last good value (with a small "couldn't refresh" note) instead of blanking the page.
 *
 * Kept free of React so it can be unit-tested in the node test environment.
 */

export interface Refreshed<T> {
  /** The new value, or the previous one when the request failed (null if there was none). */
  value: T | null;
  /** True when the request failed and an older value was kept on screen. */
  kept: boolean;
}

/**
 * How long a poll tick waits on an outstanding refresh before superseding it. The API
 * client has no fetch timeout, so without a limit a request that never settles would
 * stop the polling for good.
 */
export const MAX_REFRESH_AGE_MS = 45_000;

/**
 * Whether a poll tick should start a refresh: yes when none is outstanding, or when the
 * outstanding one started at least `maxAgeMs` ago (the new one supersedes it).
 */
export function shouldStartPoll(
  inFlightSince: number | null,
  now: number,
  maxAgeMs: number = MAX_REFRESH_AGE_MS,
): boolean {
  return inFlightSince === null || now - inFlightSince >= maxAgeMs;
}

/** One part of a refresh: take the new value, or keep the last good one on failure. */
export function keepLastGood<T>(result: PromiseSettledResult<T>, previous: T | null): Refreshed<T> {
  if (result.status === "fulfilled") return { value: result.value, kept: false };
  return { value: previous, kept: previous !== null };
}

/** A wall-clock time with seconds in the viewer's locale, e.g. "2:41:05 PM". */
export function formatClockTime(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** The non-blocking note shown while older data stays on screen after a failed refresh. */
export function staleDataMessage(updatedAt: number | null): string {
  return updatedAt === null
    ? "Couldn't refresh — showing older data."
    : `Couldn't refresh — showing data from ${formatClockTime(updatedAt)}.`;
}
