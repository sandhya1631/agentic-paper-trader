import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { BarsResponse, MarketSnapshot } from "@/lib/market-data/market-data-api";
import {
  AUTO_WARNING,
  NO_AGENT_MESSAGE,
  approvalModeOptions,
  buildConfigPatch,
  describeAgentConfigError,
  describeApprovalMode,
  formatFraction,
  isAutoMode,
  isNoAgentError,
  normalizeSymbol,
  normalizeWatchlist,
  summarizePreview,
  validateSymbol,
  validateWatchlist,
} from "./agent-config-view";

function apiError(status: number, body: unknown = null): ApiError {
  return new ApiError(status, "Error", body, "http://api/test");
}

function fulfilled<T>(value: T): PromiseSettledResult<T> {
  return { status: "fulfilled", value };
}

function rejected<T>(reason: unknown): PromiseSettledResult<T> {
  return { status: "rejected", reason };
}

const snapshot: MarketSnapshot = {
  symbol: "MSFT",
  latest_close: "410.50",
  rsi_14: "55.10",
  sma_20: "405.00",
  sma_50: null,
  bars_count: 90,
  timestamp: "2026-11-02T15:00:00Z",
};

const bar = { timestamp: "2026-11-02T15:00:00Z", open: 1, high: 2, low: 0.5, close: 409.25, volume: 10 };

describe("normalizeSymbol / normalizeWatchlist", () => {
  it("trims and upper-cases a symbol", () => {
    expect(normalizeSymbol("  brk.b ")).toBe("BRK.B");
  });

  it("uppercases, trims and de-dupes in order, dropping blanks", () => {
    expect(normalizeWatchlist(["aapl", " nvda ", "AAPL", "  "])).toEqual(["AAPL", "NVDA"]);
  });
});

describe("validateSymbol", () => {
  const current = ["AAPL", "NVDA"];

  it("accepts a well-formed new ticker", () => {
    expect(validateSymbol(" msft ", current, 20)).toBeNull();
    expect(validateSymbol("brk.b", current, null)).toBeNull();
  });

  it("rejects an empty symbol", () => {
    expect(validateSymbol("   ", current, 20)).toMatch(/enter a symbol/i);
  });

  it("rejects malformed tickers", () => {
    expect(validateSymbol("1BAD", current, 20)).toMatch(/not a valid ticker/i);
    expect(validateSymbol("TOO-LONG", current, 20)).toMatch(/not a valid ticker/i);
    expect(validateSymbol("ABCDEFGHIJK", current, 20)).toMatch(/not a valid ticker/i);
  });

  it("rejects a duplicate after normalizing", () => {
    expect(validateSymbol("aapl", current, 20)).toBe("AAPL is already in the watchlist.");
  });

  it("rejects adding to a full watchlist only when the cap is known", () => {
    expect(validateSymbol("MSFT", current, 2)).toMatch(/full \(2 symbols max\)/i);
    expect(validateSymbol("MSFT", current, null)).toBeNull();
  });
});

describe("validateWatchlist", () => {
  it("rejects an empty list", () => {
    expect(validateWatchlist([], 20)).toMatch(/at least one symbol/i);
  });

  it("rejects more symbols than the cap", () => {
    expect(validateWatchlist(["AAPL", "NVDA", "SPY"], 2)).toMatch(/at most 2 symbols/i);
  });

  it("rejects a malformed entry", () => {
    expect(validateWatchlist(["AAPL", "9X"])).toBe("9X is not a valid ticker.");
  });

  it("accepts a valid list", () => {
    expect(validateWatchlist(["AAPL", "NVDA"], 20)).toBeNull();
  });
});

describe("buildConfigPatch", () => {
  const original = { watchlist: ["AAPL", "NVDA", "SPY"], approval_mode: "REQUIRED" };

  it("is empty when nothing changed", () => {
    expect(buildConfigPatch(original, { watchlist: ["aapl", "NVDA", "SPY"], approval_mode: "REQUIRED" })).toEqual(
      {},
    );
  });

  it("includes only the changed watchlist, normalized", () => {
    expect(
      buildConfigPatch(original, { watchlist: ["aapl", " spy ", "msft", "AAPL"], approval_mode: "REQUIRED" }),
    ).toEqual({ watchlist: ["AAPL", "SPY", "MSFT"] });
  });

  it("treats a reorder as a change", () => {
    expect(
      buildConfigPatch(original, { watchlist: ["NVDA", "AAPL", "SPY"], approval_mode: "REQUIRED" }),
    ).toEqual({ watchlist: ["NVDA", "AAPL", "SPY"] });
  });

  it("includes only the changed approval mode", () => {
    expect(buildConfigPatch(original, { watchlist: original.watchlist, approval_mode: "AUTO" })).toEqual({
      approval_mode: "AUTO",
    });
  });
});

describe("approval modes", () => {
  it("identifies AUTO", () => {
    expect(isAutoMode("AUTO")).toBe(true);
    expect(isAutoMode("REQUIRED")).toBe(false);
  });

  it("describes known modes and falls back to the raw value", () => {
    expect(describeApprovalMode("REQUIRED").label).toBe("Approval required");
    expect(describeApprovalMode("OBSERVE_ONLY").help).toMatch(/nothing is executed/i);
    expect(describeApprovalMode("SHADOW")).toEqual({ label: "SHADOW", help: "" });
  });

  it("states that AUTO skips human review but not the risk policies", () => {
    expect(AUTO_WARNING).toMatch(/no human review/i);
    expect(AUTO_WARNING).toMatch(/after the risk policies pass/i);
  });

  it("builds select options from the constants, always including the current mode", () => {
    const modes = ["OBSERVE_ONLY", "REQUIRED", "AUTO"];
    expect(approvalModeOptions(modes, "AUTO")).toEqual(modes);
    expect(approvalModeOptions(modes, "SHADOW")).toEqual([...modes, "SHADOW"]);
    expect(approvalModeOptions(null, "REQUIRED")).toEqual(["REQUIRED"]);
    expect(approvalModeOptions([], "REQUIRED")).toEqual(["REQUIRED"]);
  });
});

describe("describeAgentConfigError", () => {
  it("treats a 404 on load as 'no agent yet'", () => {
    const err = apiError(404, { detail: "No agent configured yet." });
    expect(isNoAgentError(err)).toBe(true);
    expect(describeAgentConfigError(err, "load")).toBe(NO_AGENT_MESSAGE);
  });

  it("explains a 403 as a role restriction", () => {
    expect(describeAgentConfigError(apiError(403, { detail: "Forbidden" }), "save")).toMatch(
      /only operators and admins/i,
    );
    expect(describeAgentConfigError(apiError(403), "load")).toMatch(/do not have access/i);
  });

  it("surfaces the backend detail for a 422 (FastAPI list shape)", () => {
    const err = apiError(422, { detail: [{ msg: "Value error, unknown symbol ZZZZ" }] });
    expect(describeAgentConfigError(err, "save")).toMatch(/unknown symbol ZZZZ/);
  });

  it("uses the detail for a 404 on save and other statuses", () => {
    expect(describeAgentConfigError(apiError(404, { detail: "No agent configured yet." }), "save")).toBe(
      "No agent configured yet.",
    );
    expect(describeAgentConfigError(apiError(500, { detail: "config down" }), "load")).toBe("config down");
  });

  it("falls back without login wording when there is no detail", () => {
    expect(describeAgentConfigError(apiError(401), "save")).toBe("Could not save the agent configuration.");
    expect(describeAgentConfigError(apiError(409), "load")).toBe("Could not load the agent configuration.");
    expect(describeAgentConfigError("weird", "load")).toBe("Could not load the agent configuration.");
  });

  it("does not mistake other errors for 'no agent'", () => {
    expect(isNoAgentError(apiError(500))).toBe(false);
    expect(isNoAgentError(new Error("x"))).toBe(false);
  });
});

describe("formatFraction", () => {
  it("formats a fraction as an unsigned percent", () => {
    expect(formatFraction("0.10")).toBe("10%");
    expect(formatFraction("0.025")).toBe("2.5%");
    expect(formatFraction(0.05)).toBe("5%");
  });

  it("renders an em dash for missing values", () => {
    expect(formatFraction(null)).toBe("—");
    expect(formatFraction("abc")).toBe("—");
  });
});

describe("summarizePreview", () => {
  it("uses the snapshot when it loads", () => {
    const preview = summarizePreview("MSFT", fulfilled(snapshot), fulfilled<BarsResponse>({ MSFT: [bar] }));
    expect(preview).toEqual({
      symbol: "MSFT",
      latestClose: "410.50",
      rsi14: "55.10",
      sma20: "405.00",
      sma50: null,
      warning: null,
      note: null,
    });
  });

  it("falls back to the last bar close when no account is connected", () => {
    const preview = summarizePreview(
      "MSFT",
      rejected(apiError(503, { detail: "No Alpaca account connected" })),
      fulfilled<BarsResponse>({ MSFT: [bar] }),
    );
    expect(preview.latestClose).toBe("409.25");
    expect(preview.rsi14).toBeNull();
    expect(preview.warning).toBeNull();
    expect(preview.note).toMatch(/connect an alpaca paper account/i);
  });

  it("warns (without blocking) when no market data exists", () => {
    const preview = summarizePreview(
      "ZZZZ",
      rejected(apiError(400, { detail: "Error fetching market snapshot for ZZZZ" })),
      fulfilled<BarsResponse>({ ZZZZ: [] }),
    );
    expect(preview.latestClose).toBeNull();
    expect(preview.warning).toMatch(/no market data found for ZZZZ/i);
    expect(preview.note).toBeNull();
  });

  it("treats a 400 snapshot as no data even when the bars request fails", () => {
    const preview = summarizePreview(
      "ZZZZ",
      rejected(apiError(400, { detail: "Error fetching market snapshot for ZZZZ" })),
      rejected(apiError(500)),
    );
    expect(preview.warning).toMatch(/no market data found/i);
  });

  it("says it could not load when both sources fail for other reasons", () => {
    const preview = summarizePreview("MSFT", rejected(apiError(502)), rejected(apiError(500)));
    expect(preview.warning).toBeNull();
    expect(preview.note).toBe("Could not load market data for MSFT.");
  });

  it("notes missing indicators when only the snapshot fails", () => {
    const preview = summarizePreview("MSFT", rejected(apiError(502)), fulfilled<BarsResponse>({ MSFT: [bar] }));
    expect(preview.latestClose).toBe("409.25");
    expect(preview.note).toMatch(/indicators are unavailable/i);
  });
});
