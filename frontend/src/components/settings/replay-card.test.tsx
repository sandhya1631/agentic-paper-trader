// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/agent-config-api", () => ({ listStrategies: vi.fn() }));
vi.mock("@/lib/agent/replay-api", () => ({ runReplay: vi.fn() }));
vi.mock("@/lib/market-data/market-data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/market-data/market-data-api")>();
  return { ...actual, getBars: vi.fn() };
});

import { listStrategies, type Strategy } from "@/lib/agent/agent-config-api";
import { runReplay, type ReplayMetrics } from "@/lib/agent/replay-api";
import { getBars } from "@/lib/market-data/market-data-api";
import { ReplayCard } from "./replay-card";

const mockListStrategies = vi.mocked(listStrategies);
const mockRunReplay = vi.mocked(runReplay);
const mockGetBars = vi.mocked(getBars);

const STRATEGIES: Strategy[] = [
  { version: "S-001", revision: "1.5.0", name: "Momentum Confluence", summary: "RSI + SMA.", selectable_mode: "FIXED" },
  { version: "S-002", revision: "2.0.0", name: "RSI & SMA Crossover", summary: "Terser RSI/SMA.", selectable_mode: "FIXED" },
];

function metrics(overrides: Partial<ReplayMetrics> = {}): ReplayMetrics {
  return {
    strategy_version: "S-001",
    samples: 1,
    valid_decisions: 1,
    invalid_decisions: 0,
    action_counts: { BUY: 1, SELL: 0, HOLD: 0 },
    average_input_tokens: 512,
    average_latency_ms: 340,
    cumulative_return_pct: 1.23,
    buy_and_hold_return_pct: 0.98,
    ...overrides,
  };
}

function fiftyOneBars() {
  return Array.from({ length: 51 }, (_, i) => ({
    timestamp: "2026-11-01T00:00:00Z",
    open: 100 + i,
    high: 100 + i,
    low: 100 + i,
    close: 100 + i,
    volume: 1000,
  }));
}

function runButton() {
  return screen.getByRole("button", { name: /^run replay$/i }) as HTMLButtonElement;
}

beforeEach(() => {
  mockListStrategies.mockReset();
  mockRunReplay.mockReset();
  mockGetBars.mockReset();
  mockListStrategies.mockResolvedValue(STRATEGIES);
});
afterEach(() => cleanup());

describe("ReplayCard", () => {
  it("loads the strategy list and defaults to the first one", async () => {
    render(<ReplayCard canRun />);

    const select = (await screen.findByLabelText(/^strategy$/i)) as HTMLSelectElement;
    expect(select.value).toBe("S-001");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["S-001", "S-002"]);
    expect(screen.getByText("RSI + SMA.")).toBeTruthy();
  });

  it("shows a load error when the strategy list fails", async () => {
    mockListStrategies.mockRejectedValue(new Error("strategies down"));
    render(<ReplayCard canRun />);

    expect(await screen.findByText("strategies down")).toBeTruthy();
  });

  it("is read-only when canRun is false", async () => {
    render(<ReplayCard canRun={false} />);
    await screen.findByLabelText(/^strategy$/i);

    expect(screen.getByText(/only operators and admins/i)).toBeTruthy();
    expect((screen.getByLabelText(/^strategy$/i) as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText(/^symbol$/i) as HTMLInputElement).disabled).toBe(true);
    expect(runButton().disabled).toBe(true);
  });

  it("disables Run replay until a valid symbol is entered", async () => {
    render(<ReplayCard canRun />);
    await screen.findByLabelText(/^strategy$/i);

    expect(runButton().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^symbol$/i), { target: { value: "1BAD" } });
    expect(screen.getByText(/not a valid ticker/i)).toBeTruthy();
    expect(runButton().disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/^symbol$/i), { target: { value: "aapl" } });
    expect(runButton().disabled).toBe(false);
  });

  it("builds a walk-forward sample from the bars and renders the returned metrics", async () => {
    mockGetBars.mockResolvedValue({ AAPL: fiftyOneBars() });
    mockRunReplay.mockResolvedValue(metrics());
    render(<ReplayCard canRun />);
    await screen.findByLabelText(/^strategy$/i);

    fireEvent.change(screen.getByLabelText(/^symbol$/i), { target: { value: "aapl" } });
    fireEvent.click(runButton());

    await waitFor(() => expect(mockRunReplay).toHaveBeenCalledTimes(1));
    const call = mockRunReplay.mock.calls[0][0];
    expect(call.strategy_version).toBe("S-001");
    expect(call.samples).toHaveLength(1);
    expect(call.samples[0].bars_per_symbol.AAPL).toHaveLength(50); // last bar held back
    expect(call.samples[0].next_prices).toEqual({ AAPL: 150 }); // the 51st bar's close

    expect(await screen.findByText("1 valid / 0 invalid")).toBeTruthy();
    expect(screen.getByText("1 BUY · 0 SELL · 0 HOLD")).toBeTruthy();
    expect(screen.getByText("1.23%")).toBeTruthy();
    expect(screen.getByText("0.98%")).toBeTruthy();
  });

  it("shows a clear message when there are too few bars, without calling the API", async () => {
    mockGetBars.mockResolvedValue({ AAPL: fiftyOneBars().slice(0, 10) });
    render(<ReplayCard canRun />);
    await screen.findByLabelText(/^strategy$/i);

    fireEvent.change(screen.getByLabelText(/^symbol$/i), { target: { value: "aapl" } });
    fireEvent.click(runButton());

    expect(await screen.findByText(/not enough bars for AAPL/i)).toBeTruthy();
    expect(mockRunReplay).not.toHaveBeenCalled();
  });

  it("surfaces a failed replay run", async () => {
    mockGetBars.mockResolvedValue({ AAPL: fiftyOneBars() });
    mockRunReplay.mockRejectedValue(
      new ApiError(403, "Forbidden", { detail: "nope" }, "http://x"),
    );
    render(<ReplayCard canRun />);
    await screen.findByLabelText(/^strategy$/i);

    fireEvent.change(screen.getByLabelText(/^symbol$/i), { target: { value: "aapl" } });
    fireEvent.click(runButton());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/only operators and admins/i);
  });
});
