import { describe, expect, it } from "vitest";
import { rsi, sma } from "./indicators";
import { buildChartRows } from "./chart-data";
import type { OHLCVBar } from "./market-data-api";

describe("sma", () => {
  it("is null during warm-up and the rolling mean afterwards", () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });

  it("returns all nulls when the period exceeds the data", () => {
    expect(sma([1, 2], 3)).toEqual([null, null]);
  });
});

describe("rsi (Wilder)", () => {
  it("is null for the warm-up window and defined from index=period", () => {
    const values = Array.from({ length: 20 }, (_, i) => i + 1); // strictly increasing
    const out = rsi(values, 14);
    expect(out.slice(0, 14).every((v) => v === null)).toBe(true);
    expect(out[14]).not.toBeNull();
  });

  it("is 100 for a strictly increasing series (no losses)", () => {
    const out = rsi(Array.from({ length: 20 }, (_, i) => i + 1), 14);
    expect(out[19]).toBe(100);
  });

  it("is 0 for a strictly decreasing series (no gains)", () => {
    const out = rsi(Array.from({ length: 20 }, (_, i) => 100 - i), 14);
    expect(out[19]).toBe(0);
  });

  it("is 50 for a flat series (no movement)", () => {
    const out = rsi(Array.from({ length: 20 }, () => 42), 14);
    expect(out[19]).toBe(50);
  });

  it("stays within [0, 100] for a mixed series", () => {
    const values = [10, 11, 10.5, 12, 11.5, 13, 12, 14, 13.5, 15, 14, 16, 15, 17, 16.5, 18, 17, 19];
    const out = rsi(values, 14);
    const last = out[out.length - 1];
    expect(last).not.toBeNull();
    expect(last as number).toBeGreaterThanOrEqual(0);
    expect(last as number).toBeLessThanOrEqual(100);
  });
});

describe("buildChartRows", () => {
  it("maps bars to rows with derived overlays and short labels", () => {
    const bars: OHLCVBar[] = Array.from({ length: 25 }, (_, i) => ({
      timestamp: `2026-10-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      open: 100 + i,
      high: 101 + i,
      low: 99 + i,
      close: 100 + i,
      volume: 1000,
    }));
    const rows = buildChartRows(bars);
    expect(rows).toHaveLength(25);
    expect(rows[0].sma20).toBeNull(); // warm-up
    expect(rows[24].sma20).not.toBeNull();
    expect(rows[24].close).toBe(124);
    expect(typeof rows[0].label).toBe("string");
  });
});
