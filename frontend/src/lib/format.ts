/**
 * Small, dependency-free formatting + sign helpers shared across the portfolio,
 * positions, and audit views. Backend Decimals arrive as JSON strings, so every
 * helper accepts `string | number | null | undefined` and renders an em dash for
 * missing/unparseable values rather than throwing or showing `NaN`.
 */

export type Tone = "positive" | "negative" | "neutral";

/** Parse a backend numeric value (Decimal-as-string or number) to a number, or null. */
export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(n) ? n : null;
}

/** Currency formatting, e.g. `$100,000.00`. Returns an em dash for missing values. */
export function formatMoney(
  value: string | number | null | undefined,
  currency = "USD",
): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n);
}

/** A signed, already-computed number (not a fraction), e.g. `+1,250.00` / `-80.00`. */
export function formatSignedMoney(
  value: string | number | null | undefined,
  currency = "USD",
): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n)}`;
}

/**
 * Format a FRACTION as a signed percent, e.g. `0.0525` -> `+5.25%`, `-0.01` -> `-1.00%`.
 * (Alpaca's `unrealized_plpc` and our derived daily-P/L ratio are both fractions.)
 */
export function formatPercent(
  fraction: string | number | null | undefined,
  digits = 2,
): string {
  const n = toNumber(fraction);
  if (n === null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(digits)}%`;
}

/** Map a number's sign to a tone for badges/values. */
export function signTone(value: string | number | null | undefined): Tone {
  const n = toNumber(value);
  if (n === null || n === 0) return "neutral";
  return n > 0 ? "positive" : "negative";
}

/** Short, locale-aware date-time for timeline/history rows; em dash for missing. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value;
  return new Date(ms).toLocaleString();
}
