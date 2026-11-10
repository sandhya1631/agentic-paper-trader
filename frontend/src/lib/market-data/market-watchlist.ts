/**
 * Which symbols the Market page shows (#165): the caller's agent watchlist from
 * `GET /agent/config`, or the backend's `default_watchlist` from `GET /meta/constants`
 * when the caller has no agent yet (404) or the watchlist could not be read.
 *
 * Kept free of React so it can be unit-tested in the node test environment.
 */
import { isNoAgentError, normalizeWatchlist } from "@/lib/agent/agent-config-view";
import type { AgentConfig } from "@/lib/agent/agent-config-api";
import type { Constants } from "@/lib/meta/meta-api";

export interface MarketWatchlist {
  /** Tab order: canonical (trimmed, upper-case, de-duplicated), first occurrence kept. */
  symbols: string[];
  /** "agent": the caller's watchlist; "default": the backend default; "none": neither. */
  source: "agent" | "default" | "none";
  /** A short, non-blocking message when the agent's watchlist could not be read. */
  error: string | null;
}

export const WATCHLIST_ERROR_MESSAGE =
  "Couldn't load your agent's watchlist. Showing the default symbols.";
export const DEFAULTS_ERROR_MESSAGE = "Couldn't load the symbol list. Try Refresh.";
export const EMPTY_WATCHLIST_MESSAGE =
  "Your agent's watchlist is empty. Add symbols in Settings to chart them here.";

/**
 * Combine the two settled requests into the tab list. The agent's watchlist wins; a 404
 * (no agent yet) silently falls back to the default list; any other failure falls back
 * too, with a short error. The watchlist is normalized here so the tabs are right even
 * for a list stored un-normalized (e.g. `["aapl", "AAPL"]` from an earlier SQL edit).
 */
export function resolveMarketWatchlist(
  config: PromiseSettledResult<Pick<AgentConfig, "watchlist">>,
  constants: PromiseSettledResult<Pick<Constants, "default_watchlist">>,
): MarketWatchlist {
  if (config.status === "fulfilled") {
    return { symbols: normalizeWatchlist(config.value.watchlist ?? []), source: "agent", error: null };
  }
  const configError = isNoAgentError(config.reason) ? null : WATCHLIST_ERROR_MESSAGE;
  const defaults =
    constants.status === "fulfilled" ? normalizeWatchlist(constants.value.default_watchlist ?? []) : [];
  if (defaults.length === 0) {
    return { symbols: [], source: "none", error: DEFAULTS_ERROR_MESSAGE };
  }
  return { symbols: defaults, source: "default", error: configError };
}

/** Keep the current symbol while it is still listed; otherwise select the first one. */
export function selectSymbol(symbols: readonly string[], current: string | null): string | null {
  if (current !== null && symbols.includes(current)) return current;
  return symbols[0] ?? null;
}
