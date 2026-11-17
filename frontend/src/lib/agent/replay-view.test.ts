import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { OHLCVBar } from "@/lib/market-data/market-data-api";
import {
  DEMO_ACCOUNT_INFO,
  MIN_REPLAY_BARS,
  buildReplaySample,
  describeReplayError,
  formatPercentPoints,
  validateReplaySymbol,
} from "./replay-view";

function bar(close: number, i: number): OHLCVBar {
  return {
    timestamp: `2026-11-0${(i % 9) + 1}T00:00:00Z`,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1000,
  };
}

function bars(count: number): OHLCVBar[] {
  return Array.from({ length: count }, (_, i) => bar(100 + i, i));
}

describe("validateReplaySymbol", () => {
  it("rejects an empty symbol", () => {
    expect(validateReplaySymbol("  ")).toMatch(/enter a symbol/i);
  });

  it("rejects an invalid ticker", () => {
    expect(validateReplaySymbol("1BAD")).toMatch(/not a valid ticker/i);
  });

  it("accepts a valid ticker, case-insensitively", () => {
    expect(validateReplaySymbol("aapl")).toBeNull();
  });
});

describe("buildReplaySample", () => {
  it("errors when there are fewer than MIN_REPLAY_BARS bars", () => {
    const result = buildReplaySample("AAPL", bars(MIN_REPLAY_BARS - 1));
    expect(result).toEqual({
      error: `Not enough bars for AAPL (need at least ${MIN_REPLAY_BARS}, got ${MIN_REPLAY_BARS - 1}).`,
    });
  });

  it("holds back the last bar as next_prices and feeds the rest as context (no look-ahead)", () => {
    const list = bars(MIN_REPLAY_BARS);
    const result = buildReplaySample("AAPL", list);
    if ("error" in result) throw new Error("expected a sample");

    expect(result.sample.symbols).toEqual(["AAPL"]);
    expect(result.sample.bars_per_symbol.AAPL).toHaveLength(MIN_REPLAY_BARS - 1);
    expect(result.sample.bars_per_symbol.AAPL).toEqual(list.slice(0, -1));
    expect(result.sample.next_prices).toEqual({ AAPL: list[list.length - 1].close });
    expect(result.sample.account_info).toEqual(DEMO_ACCOUNT_INFO);
    expect(result.sample.market_open).toBe(true);
  });
});

describe("describeReplayError", () => {
  it("maps 403 to an operator-only message", () => {
    expect(describeReplayError(new ApiError(403, "Forbidden", {}, "http://x"), "run")).toMatch(
      /only operators and admins/i,
    );
  });

  it("prefers the backend's detail when present", () => {
    const err = new ApiError(422, "Unprocessable Entity", { detail: "bad input" }, "http://x");
    expect(describeReplayError(err, "run")).toBe("bad input");
  });

  it("falls back to a generic message without a detail", () => {
    const err = new ApiError(500, "Server Error", {}, "http://x");
    expect(describeReplayError(err, "run")).toBe("Could not run the replay.");
    expect(describeReplayError(err, "load")).toBe("Could not load the available strategies.");
  });
});

describe("formatPercentPoints", () => {
  it("formats a signed percentage to two decimal places", () => {
    expect(formatPercentPoints(1.5)).toBe("1.50%");
    expect(formatPercentPoints(-0.333)).toBe("-0.33%");
    expect(formatPercentPoints(0)).toBe("0.00%");
  });
});
