// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/market-data/market-data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/market-data/market-data-api")>();
  return { ...actual, getBars: vi.fn(), getSnapshot: vi.fn() };
});

import {
  getBars,
  getSnapshot,
  type MarketSnapshot,
  type OHLCVBar,
} from "@/lib/market-data/market-data-api";
import { MarketView } from "./market-view";

const mockBars = vi.mocked(getBars);
const mockSnapshot = vi.mocked(getSnapshot);

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
  mockSnapshot.mockResolvedValue(snapshot());
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
