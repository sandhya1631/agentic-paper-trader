import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/errors";
import {
  DEFAULTS_ERROR_MESSAGE,
  WATCHLIST_ERROR_MESSAGE,
  resolveMarketWatchlist,
  selectSymbol,
} from "./market-watchlist";

function fulfilled<T>(value: T): PromiseSettledResult<T> {
  return { status: "fulfilled", value };
}

function rejected<T>(reason: unknown): PromiseSettledResult<T> {
  return { status: "rejected", reason };
}

const DEFAULTS = fulfilled({ default_watchlist: ["AAPL", "NVDA", "SPY"] });
const notFound = new ApiError(404, "Not Found", { detail: "No agent configured yet." }, "http://x");
const serverError = new ApiError(500, "Server Error", null, "http://x");

describe("resolveMarketWatchlist", () => {
  it("uses the agent watchlist in order", () => {
    expect(resolveMarketWatchlist(fulfilled({ watchlist: ["MSFT", "AAPL"] }), DEFAULTS)).toEqual({
      symbols: ["MSFT", "AAPL"],
      source: "agent",
      error: null,
    });
  });

  it("normalizes the agent watchlist (case, whitespace, duplicates)", () => {
    const config = fulfilled({ watchlist: ["aapl", " AAPL ", "nvda", ""] });
    expect(resolveMarketWatchlist(config, DEFAULTS).symbols).toEqual(["AAPL", "NVDA"]);
  });

  it("does not fall back for an empty agent watchlist", () => {
    expect(resolveMarketWatchlist(fulfilled({ watchlist: [] }), DEFAULTS)).toEqual({
      symbols: [],
      source: "agent",
      error: null,
    });
  });

  it("uses the default list without an error when the caller has no agent (404)", () => {
    expect(resolveMarketWatchlist(rejected(notFound), DEFAULTS)).toEqual({
      symbols: ["AAPL", "NVDA", "SPY"],
      source: "default",
      error: null,
    });
  });

  it("uses the default list with a short error for any other failure", () => {
    for (const reason of [serverError, new TypeError("Failed to fetch")]) {
      expect(resolveMarketWatchlist(rejected(reason), DEFAULTS)).toEqual({
        symbols: ["AAPL", "NVDA", "SPY"],
        source: "default",
        error: WATCHLIST_ERROR_MESSAGE,
      });
    }
  });

  it("reports when no list is available at all", () => {
    const failures: PromiseSettledResult<{ watchlist: string[] }>[] = [
      rejected(notFound),
      rejected(serverError),
    ];
    for (const config of failures) {
      expect(resolveMarketWatchlist(config, rejected(serverError))).toEqual({
        symbols: [],
        source: "none",
        error: DEFAULTS_ERROR_MESSAGE,
      });
    }
  });

  it("ignores the constants when the agent watchlist loaded", () => {
    const config = fulfilled({ watchlist: ["MSFT"] });
    expect(resolveMarketWatchlist(config, rejected(serverError)).symbols).toEqual(["MSFT"]);
  });
});

describe("selectSymbol", () => {
  it("selects the first symbol initially", () => {
    expect(selectSymbol(["MSFT", "AAPL"], null)).toBe("MSFT");
  });

  it("keeps the current symbol while it is listed", () => {
    expect(selectSymbol(["MSFT", "AAPL"], "AAPL")).toBe("AAPL");
  });

  it("falls back to the first remaining symbol when the current one was removed", () => {
    expect(selectSymbol(["SPY", "AAPL"], "NVDA")).toBe("SPY");
  });

  it("is null for an empty list", () => {
    expect(selectSymbol([], "AAPL")).toBeNull();
    expect(selectSymbol([], null)).toBeNull();
  });
});
