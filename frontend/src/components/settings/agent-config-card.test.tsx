// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

// Only the API seams are mocked; the pure view helpers stay real.
vi.mock("@/lib/agent/agent-config-api", () => ({
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
}));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));
vi.mock("@/lib/market-data/market-data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/market-data/market-data-api")>();
  return { ...actual, getBars: vi.fn(), getSnapshot: vi.fn() };
});

import { getAgentConfig, updateAgentConfig, type AgentConfig } from "@/lib/agent/agent-config-api";
import { getConstants, type Constants } from "@/lib/meta/meta-api";
import { getBars, getSnapshot, type MarketSnapshot } from "@/lib/market-data/market-data-api";
import { AgentConfigCard } from "./agent-config-card";

const mockGetConfig = vi.mocked(getAgentConfig);
const mockUpdate = vi.mocked(updateAgentConfig);
const mockConstants = vi.mocked(getConstants);
const mockBars = vi.mocked(getBars);
const mockSnapshot = vi.mocked(getSnapshot);

function config(overrides: Partial<AgentConfig> = {}): AgentConfig {
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
    watchlist: ["AAPL", "NVDA", "SPY"],
    ...overrides,
  };
}

const CONSTANTS: Constants = {
  currency: "USD",
  cycle_trigger: ["MANUAL", "SCHEDULED"],
  cycle_status: ["RUNNING", "COMPLETED", "FAILED"],
  decision_action: ["BUY", "SELL", "HOLD"],
  policy_decision_type: ["ALLOW", "DENY"],
  approval_mode: ["OBSERVE_ONLY", "REQUIRED", "AUTO"],
  approval_status: ["PENDING", "APPROVED", "REJECTED"],
  max_watchlist_size: 20,
};

function snapshot(overrides: Partial<MarketSnapshot> = {}): MarketSnapshot {
  return {
    symbol: "MSFT",
    latest_close: "410.50",
    rsi_14: "55.10",
    sma_20: "405.00",
    sma_50: "398.00",
    bars_count: 90,
    timestamp: "2026-11-02T15:00:00Z",
    ...overrides,
  };
}

function saveButton() {
  return screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement;
}

beforeEach(() => {
  mockGetConfig.mockReset();
  mockUpdate.mockReset();
  mockConstants.mockReset();
  mockBars.mockReset();
  mockSnapshot.mockReset();
  mockGetConfig.mockResolvedValue(config());
  mockConstants.mockResolvedValue(CONSTANTS);
  mockSnapshot.mockResolvedValue(snapshot());
  mockBars.mockResolvedValue({});
});
afterEach(() => cleanup());

describe("AgentConfigCard", () => {
  it("renders the current config from GET /agent/config", async () => {
    render(<AgentConfigCard canEdit />);

    expect(await screen.findByText("AAPL")).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("SPY")).toBeTruthy();
    expect(screen.getByText("Watchlist (3/20)")).toBeTruthy();
    expect((screen.getByLabelText(/approval mode/i) as HTMLSelectElement).value).toBe("REQUIRED");
    // Cadence is display-only and points at the schedule; the meaningless 15 is not shown.
    expect(screen.getByText(/set by the run interval in autonomous schedule/i)).toBeTruthy();
    expect(screen.queryByText(/15 min/i)).toBeNull();
    expect(screen.getByText("10%")).toBeTruthy(); // max per symbol, read-only
    expect(screen.getByText("2%")).toBeTruthy(); // daily loss limit
    expect(saveButton().disabled).toBe(true); // nothing changed yet
  });

  it("previews, adds and removes a symbol, then saves the normalized watchlist", async () => {
    mockUpdate.mockResolvedValue(config({ watchlist: ["AAPL", "SPY", "MSFT"] }));
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText(/add symbol/i), { target: { value: " msft " } });
    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

    expect(await screen.findByText("410.50")).toBeTruthy(); // latest close
    expect(screen.getByText("55.10")).toBeTruthy(); // RSI-14
    expect(mockSnapshot).toHaveBeenCalledWith("MSFT");
    expect(mockBars).toHaveBeenCalledWith({ symbols: ["MSFT"], limit: 1, lookbackDays: 7 });

    fireEvent.click(screen.getByRole("button", { name: /add msft/i }));
    expect(screen.getByText("MSFT")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /remove nvda/i }));
    expect(screen.queryByText("NVDA")).toBeNull();

    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith({ watchlist: ["AAPL", "SPY", "MSFT"] }),
    );
    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Saved. The next cycle uses this configuration.");
    expect(saveButton().disabled).toBe(true); // saved state is the new baseline
  });

  it("lets a symbol with no market data be added after a warning", async () => {
    mockSnapshot.mockRejectedValue(
      new ApiError(400, "Bad Request", { detail: "Error fetching market snapshot for ZZZZ" }, "http://x"),
    );
    mockBars.mockResolvedValue({ ZZZZ: [] });
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText(/add symbol/i), { target: { value: "zzzz" } });
    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

    expect(await screen.findByText(/no market data found for ZZZZ/i)).toBeTruthy();
    const add = screen.getByRole("button", { name: /add zzzz/i }) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    expect(screen.getByText("ZZZZ")).toBeTruthy();
  });

  it("disables Save and explains invalid input", async () => {
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");
    const input = screen.getByLabelText(/add symbol/i);
    const preview = () => screen.getByRole("button", { name: /^preview$/i }) as HTMLButtonElement;

    fireEvent.change(input, { target: { value: "1BAD" } });
    expect(screen.getByText(/not a valid ticker/i)).toBeTruthy();
    expect(preview().disabled).toBe(true);

    fireEvent.change(input, { target: { value: "aapl" } });
    expect(screen.getByText("AAPL is already in the watchlist.")).toBeTruthy();
    expect(preview().disabled).toBe(true);

    fireEvent.change(input, { target: { value: "" } });
    for (const symbol of ["AAPL", "NVDA", "SPY"]) {
      fireEvent.click(screen.getByRole("button", { name: `Remove ${symbol}` }));
    }
    expect(screen.getByText(/at least one symbol/i)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("warns about AUTO and only saves it after an explicit confirmation", async () => {
    mockUpdate.mockResolvedValue(config({ approval_mode: "AUTO" }));
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    expect(screen.queryByText(/no human review/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/approval mode/i), { target: { value: "AUTO" } });
    expect(screen.getByText(/no human review/i).textContent).toBe(
      "AUTO — no human review: trades auto-execute after the risk policies pass.",
    );

    fireEvent.click(saveButton());
    expect(mockUpdate).not.toHaveBeenCalled(); // needs the second step
    fireEvent.click(await screen.findByRole("button", { name: /confirm auto/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ approval_mode: "AUTO" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/saved/i);
  });

  it("cancelling the AUTO confirmation leaves the mode unsaved", async () => {
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText(/approval mode/i), { target: { value: "AUTO" } });
    fireEvent.click(saveButton());
    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("button", { name: /confirm auto/i })).toBeNull();
    expect(saveButton().disabled).toBe(false);
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("saves a non-AUTO mode change without the extra confirmation", async () => {
    mockUpdate.mockResolvedValue(config({ approval_mode: "OBSERVE_ONLY" }));
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText(/approval mode/i), { target: { value: "OBSERVE_ONLY" } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ approval_mode: "OBSERVE_ONLY" }));
  });

  it("loads clean for a watchlist stored un-normalized (#179)", async () => {
    // A row stored as ["aapl", "AAPL", "nvda"] comes back from GET /agent/config normalized.
    mockGetConfig.mockResolvedValue(config({ watchlist: ["AAPL", "NVDA"] }));
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    expect(screen.getByText("Watchlist (2/20)")).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
    expect((screen.getByRole("button", { name: /^reset$/i }) as HTMLButtonElement).disabled).toBe(true);

    // Editing still makes it dirty and saves the de-duplicated list.
    mockUpdate.mockResolvedValue(config({ watchlist: ["AAPL"] }));
    fireEvent.click(screen.getByRole("button", { name: /remove nvda/i }));
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ watchlist: ["AAPL"] }));
    await waitFor(() => expect(saveButton().disabled).toBe(true));
  });

  it("shows a loading state", () => {
    mockGetConfig.mockReturnValue(new Promise(() => {}));
    render(<AgentConfigCard canEdit />);
    expect(screen.getByText(/loading agent configuration/i)).toBeTruthy();
  });

  it("shows the no-agent empty state on 404 and can check again", async () => {
    mockGetConfig.mockRejectedValueOnce(
      new ApiError(404, "Not Found", { detail: "No agent configured yet." }, "http://x"),
    );
    render(<AgentConfigCard canEdit />);

    expect(await screen.findByText(/no agent yet/i)).toBeTruthy();
    expect(screen.getByText(/trigger now/i)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull(); // empty state, not an error

    fireEvent.click(screen.getByRole("button", { name: /check again/i }));
    expect(await screen.findByText("AAPL")).toBeTruthy();
  });

  it("shows a load error distinctly", async () => {
    mockGetConfig.mockRejectedValue(
      new ApiError(500, "Server Error", { detail: "config down" }, "http://x"),
    );
    render(<AgentConfigCard canEdit />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("config down");
    expect(screen.queryByText(/no agent yet/i)).toBeNull();
  });

  it("surfaces a failed save", async () => {
    mockUpdate.mockRejectedValue(
      new ApiError(
        422,
        "Unprocessable Entity",
        { detail: [{ msg: "Value error, unknown symbol ZZZZ" }] },
        "http://x",
      ),
    );
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    fireEvent.click(screen.getByRole("button", { name: /remove nvda/i }));
    fireEvent.click(saveButton());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/unknown symbol ZZZZ/i);
    expect(saveButton().disabled).toBe(false); // the draft is kept for another try
  });

  it("falls back to the current mode when the constants fail to load", async () => {
    mockConstants.mockRejectedValue(new Error("constants down"));
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    const select = screen.getByLabelText(/approval mode/i) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["REQUIRED"]);
    expect(screen.getByText("Watchlist")).toBeTruthy(); // no cap shown when unknown
  });

  it("is read-only for viewers", async () => {
    render(<AgentConfigCard canEdit={false} />);
    await screen.findByText("AAPL");

    expect(screen.getByText(/only operators and admins/i)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
    expect((screen.getByRole("button", { name: /remove aapl/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByLabelText(/add symbol/i) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/approval mode/i) as HTMLSelectElement).disabled).toBe(true);
  });
});
