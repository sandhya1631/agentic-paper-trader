/**
 * Pure view helpers for the Settings "Strategy replay" card (#194).
 *
 * Kept free of React so they can be unit-tested in the node test environment. This is a
 * read-only sanity check: it never creates cycles, reads the broker, or submits orders.
 */
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";
import type { OHLCVBar } from "@/lib/market-data/market-data-api";
import { normalizeSymbol, TICKER_PATTERN } from "./agent-config-view";
import type { ReplaySample } from "./replay-api";

/**
 * `build_agent_context`'s indicators need 50+ bars for a non-null SMA-50 (matching the
 * live-cycle collector), plus one more bar held back as the sample's "next price" so the
 * replay can score the decision without look-ahead (the held-back bar is never part of
 * what the model sees).
 */
export const MIN_REPLAY_BARS = 51;

/** A fixed, clearly-fake paper account so the model reasons with LIVE account data instead
 * of defaulting to HOLD on UNAVAILABLE (the prompt's mandated response otherwise). */
export const DEMO_ACCOUNT_INFO = {
  cash: "50000.00",
  equity: "50000.00",
  buying_power: "50000.00",
  daily_pnl: "0.00",
};

export function validateReplaySymbol(raw: string): string | null {
  const symbol = normalizeSymbol(raw);
  if (!symbol) return "Enter a symbol.";
  if (!TICKER_PATTERN.test(symbol)) {
    return "Not a valid ticker: 1–10 letters, digits or dots, starting with a letter.";
  }
  return null;
}

export type ReplaySampleResult = { sample: ReplaySample } | { error: string };

/**
 * Split the most recent bars into the context the model sees and the one bar held back as
 * the outcome, so the replay is walk-forward (no look-ahead) just like the live collector.
 */
export function buildReplaySample(symbol: string, bars: readonly OHLCVBar[]): ReplaySampleResult {
  if (bars.length < MIN_REPLAY_BARS) {
    return {
      error: `Not enough bars for ${symbol} (need at least ${MIN_REPLAY_BARS}, got ${bars.length}).`,
    };
  }
  const context = bars.slice(0, -1);
  const held = bars[bars.length - 1];
  return {
    sample: {
      symbols: [symbol],
      bars_per_symbol: { [symbol]: context },
      account_info: DEMO_ACCOUNT_INFO,
      positions: [],
      open_orders: [],
      market_open: true,
      next_prices: { [symbol]: held.close },
    },
  };
}

function hasDetail(error: ApiError): boolean {
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" || (Array.isArray(detail) && detail.length > 0);
}

/** Map a failed strategy-list load or replay run to a distinct, readable message. */
export function describeReplayError(error: unknown, action: "load" | "run"): string {
  const fallback =
    action === "load" ? "Could not load the available strategies." : "Could not run the replay.";
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return "Only operators and admins can run a strategy replay.";
    }
    return hasDetail(error) ? getErrorMessage(error, fallback) : fallback;
  }
  return getErrorMessage(error, fallback);
}

/** A ratio already expressed in percentage points (e.g. 1.23 -> "1.23%"), not a fraction. */
export function formatPercentPoints(value: number): string {
  return `${value.toFixed(2)}%`;
}
