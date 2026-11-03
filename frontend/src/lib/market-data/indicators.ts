/**
 * Display-only technical indicators for the market-data charts.
 *
 * The backend computes the AUTHORITATIVE RSI-14 / SMA-20 / SMA-50 for the agent
 * (see backend/app/market_data/indicators.py) and exposes the latest values via
 * `/broker/snapshot/{symbol}`. The `/market-data/bars` endpoint, however, returns
 * only OHLCV, so to draw the overlay *series* on the chart we recompute the same
 * indicators here, client-side, using the same methods as the backend:
 *   - SMA: simple rolling mean.
 *   - RSI-14: Wilder's smoothing, seeded with the simple average of the first
 *     `period` price differences.
 * These are labelled "derived" in the UI; they are for visualization only and are
 * never used to make or validate a trade (that stays server-side — see CLAUDE.md
 * three-layer safety model).
 */

/** Simple moving average aligned to `values`; `null` until `period` samples exist. */
export function sma(values: number[], period: number): (number | null)[] {
  if (period <= 0) return values.map(() => null);
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    out.push(i >= period - 1 ? sum / period : null);
  }
  return out;
}

/**
 * Wilder's RSI aligned to `values`; `null` for the warm-up window (the first
 * `period` entries). Matches the backend's seeding (simple average of the first
 * `period` differences) and recursive smoothing, so the final point agrees with
 * the snapshot endpoint for the same bars.
 */
export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = values.map(() => null);
  if (values.length <= period) return out;

  const gains: number[] = [0];
  const losses: number[] = [0];
  for (let i = 1; i < values.length; i += 1) {
    const diff = values[i] - values[i - 1];
    gains.push(diff > 0 ? diff : 0);
    losses.push(diff < 0 ? -diff : 0);
  }

  // Seed: simple average of the first `period` differences (indices 1..period).
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i += 1) {
    avgGain += gains[i];
    avgLoss += losses[i];
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = toRsi(avgGain, avgLoss);

  // Wilder's smoothing forward.
  for (let i = period + 1; i < values.length; i += 1) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period;
    out[i] = toRsi(avgGain, avgLoss);
  }
  return out;
}

function toRsi(avgGain: number, avgLoss: number): number {
  if (avgGain === 0 && avgLoss === 0) return 50;
  if (avgLoss === 0) return 100;
  if (avgGain === 0) return 0;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}
