/**
 * Turns raw OHLCV bars into chart rows with derived SMA-20 / SMA-50 / RSI-14
 * overlays. Pure (no React / no chart lib) so it unit-tests in the node env and
 * the chart component just renders the result.
 */
import type { OHLCVBar } from "./market-data-api";
import { rsi, sma } from "./indicators";

export interface ChartRow {
  timestamp: string;
  /** Short axis label, e.g. "Nov 2". */
  label: string;
  close: number;
  sma20: number | null;
  sma50: number | null;
  rsi14: number | null;
}

function shortDate(timestamp: string): string {
  const ms = Date.parse(timestamp);
  if (!Number.isFinite(ms)) return timestamp;
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function buildChartRows(bars: OHLCVBar[]): ChartRow[] {
  const closes = bars.map((b) => b.close);
  const sma20 = sma(closes, 20);
  const sma50 = sma(closes, 50);
  const rsi14 = rsi(closes, 14);
  return bars.map((bar, i) => ({
    timestamp: bar.timestamp,
    label: shortDate(bar.timestamp),
    close: bar.close,
    sma20: sma20[i],
    sma50: sma50[i],
    rsi14: rsi14[i],
  }));
}
