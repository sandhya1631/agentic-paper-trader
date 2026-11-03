/**
 * Market-data API seam — typed wrappers over the read-only market-data endpoints
 * (#142). The shared api client attaches the Bearer token and handles 401 globally.
 *
 * NOTE: `/market-data/bars` may return stale bars in this environment (a known
 * collector bug returns the oldest bars in the lookback window, not the newest).
 * The UI handles that by flagging staleness rather than hiding it — see
 * `freshness.ts`.
 */
import { api } from "@/lib/api/client";

/** One OHLCV price bar. Numeric fields arrive as JSON numbers. */
export interface OHLCVBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** `/market-data/bars` returns a map of symbol -> bars (oldest to newest). */
export type BarsResponse = Record<string, OHLCVBar[]>;

/** `/broker/snapshot/{symbol}` — latest close + indicators (Decimals as strings). */
export interface MarketSnapshot {
  symbol: string;
  latest_close: string;
  rsi_14: string | null;
  sma_20: string | null;
  sma_50: string | null;
  bars_count: number;
  timestamp: string | null;
}

/** The backend's default tracked symbols (watchlist editing is a separate story). */
export const DEFAULT_SYMBOLS = ["AAPL", "NVDA", "SPY"] as const;

export interface BarsParams {
  symbols?: readonly string[];
  limit?: number;
  lookbackDays?: number;
}

export function getBars(params: BarsParams = {}): Promise<BarsResponse> {
  const query = new URLSearchParams();
  for (const symbol of params.symbols ?? []) query.append("symbols", symbol);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.lookbackDays != null) query.set("lookback_days", String(params.lookbackDays));
  const qs = query.toString();
  return api.get<BarsResponse>(`/market-data/bars${qs ? `?${qs}` : ""}`);
}

export function getSnapshot(symbol: string): Promise<MarketSnapshot> {
  return api.get<MarketSnapshot>(`/api/v1/broker/snapshot/${encodeURIComponent(symbol)}`);
}
