// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/market-data/market-data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/market-data/market-data-api")>();
  return { ...actual, getBars: vi.fn(), getSnapshot: vi.fn() };
});
vi.mock("@/lib/agent/agent-config-api", () => ({
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
}));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));

import { getAgentConfig, type AgentConfig } from "@/lib/agent/agent-config-api";
import { getConstants, type Constants } from "@/lib/meta/meta-api";
import {
  getBars,
  getSnapshot,
  type MarketSnapshot,
  type OHLCVBar,
} from "@/lib/market-data/market-data-api";
import { MarketView } from "./market-view";

const mockBars = vi.mocked(getBars);
const mockSnapshot = vi.mocked(getSnapshot);
const mockGetConfig = vi.mocked(getAgentConfig);
const mockConstants = vi.mocked(getConstants);

function agentConfig(watchlist: string[]): AgentConfig {
  return {
    id: "a1",
    name: "Default agent",
    strategy_version: "S-001",
    cadence_minutes: 15,
    target_allocation: "0.05",
    max_symbol_allocation: "0.10",
    cash_reserve: "0.05",
    daily_loss_limit: "0.02",
    approval_mode: "REQUIRED",
    enabled: true,
    watchlist,
  };
}

function constants(defaultWatchlist: string[] = ["AAPL", "NVDA", "SPY"]): Constants {
  return {
    currency: "USD",
    cycle_trigger: ["MANUAL", "SCHEDULED"],
    cycle_status: ["RUNNING", "COMPLETED", "FAILED"],
    decision_action: ["BUY", "SELL", "HOLD"],
    policy_decision_type: ["ALLOW", "DENY"],
    approval_mode: ["OBSERVE_ONLY", "REQUIRED", "AUTO"],
    approval_status: ["PENDING", "APPROVED", "REJECTED"],
    max_watchlist_size: 20,
    default_watchlist: defaultWatchlist,
  };
}

function tabNames(): string[] {
  return screen.queryAllByRole("tab").map((tab) => tab.textContent ?? "");
}

function selectedTab(): string | null {
  const selected = screen
    .queryAllByRole("tab")
    .find((tab) => tab.getAttribute("aria-selected") === "true");
  return selected?.textContent ?? null;
}

function noAgent(): ApiError {
  return new ApiError(404, "Not Found", { detail: "No agent configured yet." }, "http://x");
}

function bars(latestTimestamp: string, n = 3): OHLCVBar[] {
  const base = Date.parse(latestTimestamp);
  return Array.from({ length: n }, (_, i) => {
    const ts = new Date(base - (n - 1 - i) * 86_400_000).toISOString();
    return { timestamp: ts, open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 };
  });
}

function snapshot(overrides: Partial<MarketSnapshot> = {}): MarketSnapshot {
  return {
    symbol: "AAPL",
    latest_close: "123.45",
    rsi_14: "58.90",
    sma_20: "120.00",
    sma_50: "118.00",
    bars_count: 90,
    timestamp: "2026-11-02T15:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockBars.mockReset();
  mockSnapshot.mockReset();
  mockGetConfig.mockReset();
  mockConstants.mockReset();
  mockSnapshot.mockResolvedValue(snapshot());
  mockGetConfig.mockResolvedValue(agentConfig(["AAPL", "NVDA", "SPY"]));
  mockConstants.mockResolvedValue(constants());
});

afterEach(() => cleanup());

describe("MarketView", () => {
  it("renders the snapshot, symbol tabs, and the chart series legend", async () => {
    mockBars.mockResolvedValue({ AAPL: bars(new Date().toISOString()) });

    render(<MarketView />);

    expect(await screen.findByText("123.45")).toBeTruthy(); // latest close
    expect(screen.getByText("58.90")).toBeTruthy(); // RSI-14 snapshot
    expect(screen.getByRole("tab", { name: "AAPL" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "NVDA" })).toBeTruthy();
    // The chart renders with an accessible summary and its own series legend
    // (scoped so the SMA labels don't collide with the snapshot stat cards).
    expect(screen.getByRole("img", { name: /AAPL price chart/ })).toBeTruthy();
    const legend = screen.getByRole("list", { name: "Chart series" });
    expect(within(legend).getByText("Close")).toBeTruthy();
    expect(within(legend).getByText("SMA-20")).toBeTruthy();
    expect(within(legend).getByText("SMA-50")).toBeTruthy();
    expect(within(legend).getByText(/RSI-14/)).toBeTruthy();
  });

  it("flags fresh data when the latest bar is recent", async () => {
    mockBars.mockResolvedValue({ AAPL: bars(new Date(Date.now() - 60_000).toISOString()) });

    render(<MarketView />);

    expect(await screen.findByText("FRESH")).toBeTruthy();
  });

  it("flags stale data when the latest bar is old and explains the denial", async () => {
    mockBars.mockResolvedValue({ AAPL: bars(new Date(Date.now() - 3 * 86_400_000).toISOString()) });

    render(<MarketView />);

    expect(await screen.findByText("STALE")).toBeTruthy();
    expect(screen.getByText(/P-004/)).toBeTruthy();
  });

  it("switches symbols and reloads", async () => {
    mockBars.mockResolvedValue({ AAPL: bars(new Date().toISOString()) });
    render(<MarketView />);
    await screen.findByText("123.45");

    mockSnapshot.mockResolvedValueOnce(snapshot({ symbol: "NVDA", latest_close: "500.00" }));
    mockBars.mockResolvedValueOnce({ NVDA: bars(new Date().toISOString()) });
    fireEvent.click(screen.getByRole("tab", { name: "NVDA" }));

    expect(await screen.findByText("500.00")).toBeTruthy();
    expect(mockBars).toHaveBeenCalledWith(
      expect.objectContaining({ symbols: ["NVDA"] }),
    );
  });

  it("shows an empty state when there are no bars", async () => {
    mockBars.mockResolvedValue({ AAPL: [] });

    render(<MarketView />);

    expect(await screen.findByText(/No price bars available/)).toBeTruthy();
  });

  it("surfaces a bars load error distinctly", async () => {
    mockBars.mockRejectedValue(new ApiError(500, "Server Error", { detail: "bars kaboom" }, "http://x"));

    render(<MarketView />);

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("bars kaboom"))).toBe(true);
  });

  it("prompts to connect when the snapshot needs an account", async () => {
    mockBars.mockResolvedValue({ AAPL: bars(new Date().toISOString()) });
    mockSnapshot.mockRejectedValue(
      new ApiError(503, "Service Unavailable", { detail: "No Alpaca account connected" }, "http://x"),
    );

    render(<MarketView />);

    expect(await screen.findByText(/Connect an Alpaca paper account/)).toBeTruthy();
  });
});

describe("MarketView watchlist tabs (#165)", () => {
  beforeEach(() => {
    mockBars.mockImplementation(async ({ symbols = [] } = {}) =>
      Object.fromEntries(symbols.map((s) => [s, bars(new Date().toISOString())])),
    );
  });

  it("builds the tabs from the agent watchlist, in order, with the first selected", async () => {
    mockGetConfig.mockResolvedValue(agentConfig(["MSFT", "AAPL"]));

    render(<MarketView />);

    await waitFor(() => expect(tabNames()).toEqual(["MSFT", "AAPL"]));
    expect(selectedTab()).toBe("MSFT");
    await waitFor(() =>
      expect(mockBars).toHaveBeenCalledWith(expect.objectContaining({ symbols: ["MSFT"] })),
    );
    expect(mockSnapshot).toHaveBeenCalledWith("MSFT");
    expect(mockSnapshot).not.toHaveBeenCalledWith("AAPL");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("normalizes a watchlist stored un-normalized", async () => {
    mockGetConfig.mockResolvedValue(agentConfig(["msft", " MSFT ", "aapl"]));

    render(<MarketView />);

    await waitFor(() => expect(tabNames()).toEqual(["MSFT", "AAPL"]));
    expect(selectedTab()).toBe("MSFT");
  });

  it("falls back to the backend default_watchlist when there is no agent yet (404)", async () => {
    mockGetConfig.mockRejectedValue(noAgent());
    mockConstants.mockResolvedValue(constants(["QQQ", "IWM"]));

    render(<MarketView />);

    await waitFor(() => expect(tabNames()).toEqual(["QQQ", "IWM"]));
    expect(selectedTab()).toBe("QQQ");
    await waitFor(() =>
      expect(mockBars).toHaveBeenCalledWith(expect.objectContaining({ symbols: ["QQQ"] })),
    );
    // No agent yet is an expected state, not an error.
    expect(screen.queryByText(/couldn't load/i)).toBeNull();
  });

  it("shows a short error and the default tabs when the watchlist fails to load", async () => {
    mockGetConfig.mockRejectedValue(new ApiError(500, "Server Error", { detail: "boom" }, "http://x"));
    mockConstants.mockResolvedValue(constants(["QQQ", "IWM"]));

    render(<MarketView />);

    expect(await screen.findByText(/couldn't load your agent's watchlist/i)).toBeTruthy();
    expect(tabNames()).toEqual(["QQQ", "IWM"]);
    expect(selectedTab()).toBe("QQQ");
  });

  it("explains when neither the watchlist nor the default list could be loaded", async () => {
    mockGetConfig.mockRejectedValue(noAgent());
    mockConstants.mockRejectedValue(new ApiError(500, "Server Error", null, "http://x"));

    render(<MarketView />);

    expect(await screen.findByText(/couldn't load the symbol list/i)).toBeTruthy();
    expect(tabNames()).toEqual([]);
    expect(mockBars).not.toHaveBeenCalled();
  });

  it("shows a placeholder in the tab row while the watchlist loads", () => {
    mockGetConfig.mockReturnValue(new Promise(() => {}));

    render(<MarketView />);

    expect(screen.getByText(/loading watchlist/i)).toBeTruthy();
    expect(tabNames()).toEqual([]);
    expect(mockBars).not.toHaveBeenCalled();
    expect(mockSnapshot).not.toHaveBeenCalled();
  });

  it("says so when the agent's watchlist is empty", async () => {
    mockGetConfig.mockResolvedValue(agentConfig([]));

    render(<MarketView />);

    expect(await screen.findByText(/watchlist is empty/i)).toBeTruthy();
    expect(tabNames()).toEqual([]);
    expect(mockBars).not.toHaveBeenCalled();
  });

  it("selects the first remaining symbol when Refresh drops the selected one", async () => {
    render(<MarketView />);
    await waitFor(() => expect(tabNames()).toEqual(["AAPL", "NVDA", "SPY"]));
    fireEvent.click(screen.getByRole("tab", { name: "NVDA" }));
    await waitFor(() => expect(mockSnapshot).toHaveBeenCalledWith("NVDA"));

    mockGetConfig.mockResolvedValue(agentConfig(["SPY", "AAPL", "MSFT"]));
    mockBars.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(tabNames()).toEqual(["SPY", "AAPL", "MSFT"]));
    expect(selectedTab()).toBe("SPY");
    await waitFor(() =>
      expect(mockBars).toHaveBeenCalledWith(expect.objectContaining({ symbols: ["SPY"] })),
    );
    expect(mockBars).not.toHaveBeenCalledWith(expect.objectContaining({ symbols: ["NVDA"] }));
  });

  it("keeps the selected symbol on Refresh while it is still listed, and reloads its data", async () => {
    render(<MarketView />);
    await waitFor(() => expect(tabNames()).toEqual(["AAPL", "NVDA", "SPY"]));
    fireEvent.click(screen.getByRole("tab", { name: "SPY" }));
    await waitFor(() => expect(mockSnapshot).toHaveBeenCalledWith("SPY"));

    mockGetConfig.mockResolvedValue(agentConfig(["MSFT", "SPY"]));
    mockSnapshot.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(tabNames()).toEqual(["MSFT", "SPY"]));
    expect(selectedTab()).toBe("SPY");
    await waitFor(() => expect(mockSnapshot).toHaveBeenCalledWith("SPY"));
    expect(mockGetConfig).toHaveBeenCalledTimes(2);
  });

  it("clears the old symbol's data when a Refresh leaves no symbols, ignoring a late answer", async () => {
    mockGetConfig.mockResolvedValue(agentConfig(["MSFT"]));
    mockSnapshot.mockResolvedValue(snapshot({ symbol: "MSFT", latest_close: "123.45" }));
    render(<MarketView />);
    expect(await screen.findByText("123.45")).toBeTruthy();
    expect(screen.getByText("FRESH")).toBeTruthy();

    // A Refresh that keeps MSFT starts a reload whose answer is still pending...
    let resolveBars!: (value: Awaited<ReturnType<typeof getBars>>) => void;
    let resolveSnapshot!: (value: MarketSnapshot) => void;
    mockBars.mockReturnValueOnce(new Promise((resolve) => (resolveBars = resolve)));
    mockSnapshot.mockReturnValueOnce(new Promise((resolve) => (resolveSnapshot = resolve)));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/loading msft market data/i)).toBeTruthy();

    // ...then the next Refresh finds the watchlist empty.
    mockGetConfig.mockResolvedValue(agentConfig([]));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/watchlist is empty/i)).toBeTruthy();
    expect(tabNames()).toEqual([]);
    expect(screen.queryByText("123.45")).toBeNull();
    expect(screen.queryByText("FRESH")).toBeNull();
    expect(screen.getByText("No data")).toBeTruthy();
    expect(screen.queryByText(/loading msft market data/i)).toBeNull();

    // The late MSFT answer is ignored.
    await act(async () => {
      resolveBars({ MSFT: bars(new Date().toISOString()) });
      resolveSnapshot(snapshot({ symbol: "MSFT", latest_close: "500.00" }));
    });
    expect(screen.queryByText("500.00")).toBeNull();
    expect(screen.queryByText("FRESH")).toBeNull();
    expect(screen.getByText("No data")).toBeTruthy();
  });

  it("renders a full watchlist of max_watchlist_size symbols in a wrapping tab row", async () => {
    const symbols = Array.from({ length: 20 }, (_, i) => `SYM${String.fromCharCode(65 + i)}`);
    mockGetConfig.mockResolvedValue(agentConfig(symbols));

    render(<MarketView />);

    await waitFor(() => expect(tabNames()).toEqual(symbols));
    expect(screen.getByRole("tablist").className).toContain("flex-wrap");
  });
});
