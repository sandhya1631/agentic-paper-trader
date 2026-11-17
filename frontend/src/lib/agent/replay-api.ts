/**
 * Offline strategy replay (#194): score a strategy against caller-supplied historical
 * samples without creating cycles, reading the broker, or submitting orders. The shared
 * api client attaches the Bearer token and handles 401 globally.
 */
import { api } from "@/lib/api/client";
import type { OHLCVBar } from "@/lib/market-data/market-data-api";

/** One historical decision point: the bars the model sees, plus its actual next price. */
export interface ReplaySample {
  symbols: string[];
  bars_per_symbol: Record<string, OHLCVBar[]>;
  account_info?: Record<string, unknown> | null;
  positions?: unknown[] | null;
  open_orders?: unknown[] | null;
  market_open?: boolean | null;
  /** Latest close one bar after `bars_per_symbol`, for scoring the decision's one-step return. */
  next_prices?: Record<string, number> | null;
}

export interface ReplayRequest {
  strategy_version: string;
  samples: ReplaySample[];
}

/** `POST /agent/replay` response: aggregated quality and one-step performance metrics. */
export interface ReplayMetrics {
  strategy_version: string;
  samples: number;
  valid_decisions: number;
  invalid_decisions: number;
  action_counts: Record<string, number>;
  average_input_tokens: number;
  average_latency_ms: number;
  cumulative_return_pct: number;
  buy_and_hold_return_pct: number;
}

export function runReplay(request: ReplayRequest): Promise<ReplayMetrics> {
  return api.post<ReplayMetrics>("/agent/replay", request);
}
