// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

// Only the API seams are mocked; the pure view helpers stay real.
vi.mock("@/lib/agent/agent-config-api", () => ({
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
  listStrategies: vi.fn(),
  updateAgentStrategy: vi.fn(),
}));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));
vi.mock("@/lib/agent/observability-api", () => ({ getLlmConfig: vi.fn() }));
vi.mock("@/lib/market-data/market-data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/market-data/market-data-api")>();
  return { ...actual, getBars: vi.fn(), getSnapshot: vi.fn() };
});

import {
  getAgentConfig,
  listStrategies,
  updateAgentConfig,
  updateAgentStrategy,
  type AgentConfig,
  type Strategy,
} from "@/lib/agent/agent-config-api";
import { getConstants, type Constants } from "@/lib/meta/meta-api";
import { getLlmConfig } from "@/lib/agent/observability-api";
import { getBars, getSnapshot, type MarketSnapshot } from "@/lib/market-data/market-data-api";
import { AgentConfigCard } from "./agent-config-card";

const mockGetConfig = vi.mocked(getAgentConfig);
const mockUpdate = vi.mocked(updateAgentConfig);
const mockConstants = vi.mocked(getConstants);
const mockBars = vi.mocked(getBars);
const mockSnapshot = vi.mocked(getSnapshot);
const mockListStrategies = vi.mocked(listStrategies);
const mockUpdateStrategy = vi.mocked(updateAgentStrategy);
const mockLlmConfig = vi.mocked(getLlmConfig);

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
    llm_timeout_seconds: 180,
    llm_keep_alive_minutes: 10,
    llm_timeout_default_seconds: 180,
    llm_keep_alive_default_minutes: 10,
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
  default_watchlist: ["AAPL", "NVDA", "SPY"],
  llm_timeout_seconds_range: [30, 900],
  llm_keep_alive_minutes_range: [-1, 240],
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

const STRATEGIES: Strategy[] = [
  { version: "S-001", revision: "1.5.0", name: "Momentum Confluence", summary: "RSI + SMA.", selectable_mode: "FIXED" },
  { version: "S-002", revision: "2.0.0", name: "RSI & SMA Crossover", summary: "Terser RSI/SMA.", selectable_mode: "FIXED" },
  { version: "DYNAMIC", revision: "runtime", name: "Dynamic (auto-selected)", summary: "Auto-picks per cycle.", selectable_mode: "DYNAMIC" },
];

function saveButton() {
  return screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement;
}

function saveStrategyButton() {
  return screen.getByRole("button", { name: /^save strategy$/i }) as HTMLButtonElement;
}

beforeEach(() => {
  mockGetConfig.mockReset();
  mockUpdate.mockReset();
  mockConstants.mockReset();
  mockBars.mockReset();
  mockSnapshot.mockReset();
  mockListStrategies.mockReset();
  mockUpdateStrategy.mockReset();
  mockGetConfig.mockResolvedValue(config());
  mockConstants.mockResolvedValue(CONSTANTS);
  mockSnapshot.mockResolvedValue(snapshot());
  mockBars.mockResolvedValue({});
  mockListStrategies.mockResolvedValue(STRATEGIES);
  mockLlmConfig.mockReset();
  mockLlmConfig.mockResolvedValue({ provider: "ollama", model: "llama3.1" });
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
    expect((screen.getByLabelText(/^strategy$/i) as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /^save strategy$/i })).toBeNull();
  });

  describe("strategy picker (#189)", () => {
    it("loads the strategy list and shows the current strategy's summary", async () => {
      render(<AgentConfigCard canEdit />);
      await screen.findByText("AAPL");

      const select = screen.getByLabelText(/^strategy$/i) as HTMLSelectElement;
      expect(select.value).toBe("S-001");
      expect(Array.from(select.options).map((o) => o.value)).toEqual([
        "S-001",
        "S-002",
        "DYNAMIC",
      ]);
      expect(screen.getByText("RSI + SMA.")).toBeTruthy();
      expect(saveStrategyButton().disabled).toBe(true); // nothing changed yet
    });

    it("falls back to the agent's current strategy when the list fails to load", async () => {
      mockListStrategies.mockRejectedValue(new Error("strategies down"));
      render(<AgentConfigCard canEdit />);
      await screen.findByText("AAPL");

      const select = screen.getByLabelText(/^strategy$/i) as HTMLSelectElement;
      expect(Array.from(select.options).map((o) => o.value)).toEqual(["S-001"]);
      expect(screen.getByText("strategies down")).toBeTruthy();
    });

    it("saves a chosen strategy with PUT /agent/{id}/strategy", async () => {
      mockUpdateStrategy.mockResolvedValue(config({ strategy_version: "S-002" }));
      render(<AgentConfigCard canEdit />);
      await screen.findByText("AAPL");

      fireEvent.change(screen.getByLabelText(/^strategy$/i), { target: { value: "S-002" } });
      expect(screen.getByText("Terser RSI/SMA.")).toBeTruthy();
      expect(saveStrategyButton().disabled).toBe(false);

      fireEvent.click(saveStrategyButton());

      await waitFor(() => expect(mockUpdateStrategy).toHaveBeenCalledWith("a1", "S-002"));
      expect((await screen.findByRole("status")).textContent).toMatch(/saved/i);
      expect(saveStrategyButton().disabled).toBe(true); // saved state is the new baseline
    });

    it("keeps the watchlist editable after a strategy save (#214)", async () => {
      // The route's real response since #214: the same shape as GET /agent/config.
      mockUpdateStrategy.mockResolvedValue(config({ strategy_version: "S-002" }));
      mockUpdate.mockResolvedValue(
        config({ strategy_version: "S-002", watchlist: ["AAPL", "SPY", "MSFT"] }),
      );
      render(<AgentConfigCard canEdit />);
      await screen.findByText("AAPL");

      fireEvent.change(screen.getByLabelText(/^strategy$/i), { target: { value: "S-002" } });
      fireEvent.click(saveStrategyButton());
      await waitFor(() => expect(saveStrategyButton().disabled).toBe(true));

      expect(screen.getByText("Watchlist (3/20)")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: /remove nvda/i }));
      fireEvent.change(screen.getByLabelText(/add symbol/i), { target: { value: "msft" } });
      fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
      fireEvent.click(await screen.findByRole("button", { name: /add msft/i }));
      fireEvent.click(saveButton());

      await waitFor(() =>
        expect(mockUpdate).toHaveBeenCalledWith({ watchlist: ["AAPL", "SPY", "MSFT"] }),
      );
      await waitFor(() => expect(saveButton().disabled).toBe(true)); // saved = new baseline
      expect(screen.getByText("MSFT")).toBeTruthy();
      expect(screen.queryByText("NVDA")).toBeNull();
    });

    it("shows a clear message on a 409 (active cycle)", async () => {
      mockUpdateStrategy.mockRejectedValue(
        new ApiError(
          409,
          "Conflict",
          { detail: "Cannot change strategy while the agent has an active cycle." },
          "http://x",
        ),
      );
      render(<AgentConfigCard canEdit />);
      await screen.findByText("AAPL");

      fireEvent.change(screen.getByLabelText(/^strategy$/i), { target: { value: "S-002" } });
      fireEvent.click(saveStrategyButton());

      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/active cycle/i);
    });

    it("is read-only for viewers, with no Save strategy button", async () => {
      render(<AgentConfigCard canEdit={false} />);
      await screen.findByText("AAPL");

      expect((screen.getByLabelText(/^strategy$/i) as HTMLSelectElement).disabled).toBe(true);
      expect(screen.queryByRole("button", { name: /^save strategy$/i })).toBeNull();
    });
  });
});

describe("AgentConfigCard: Ollama timeout and keep-alive (#203)", () => {
  function timeoutInput() {
    return screen.getByLabelText(/llm request timeout/i) as HTMLInputElement;
  }
  function keepAliveInput() {
    return screen.getByLabelText(/model keep-alive/i) as HTMLInputElement;
  }
  function saveButton() {
    return screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement;
  }

  it("shows both fields with their effective values and the server defaults", async () => {
    mockGetConfig.mockResolvedValue(config({ llm_timeout_seconds: 240, llm_keep_alive_minutes: 30 }));
    render(<AgentConfigCard canEdit />);

    expect((await screen.findByLabelText(/llm request timeout/i) as HTMLInputElement).value).toBe("240");
    expect(keepAliveInput().value).toBe("30");
    expect(screen.getByText(/default 180 s/i)).toBeTruthy();
    expect(screen.getByText(/default 10 min/i)).toBeTruthy();
    expect(screen.getByText(/keeps the model loaded for 30 min/i)).toBeTruthy();
  });

  it("sends only the changed values in the PATCH", async () => {
    mockUpdate.mockResolvedValue(config({ llm_timeout_seconds: 300, llm_keep_alive_minutes: -1 }));
    render(<AgentConfigCard canEdit />);
    await screen.findByLabelText(/llm request timeout/i);

    fireEvent.change(timeoutInput(), { target: { value: "300" } });
    fireEvent.change(keepAliveInput(), { target: { value: "-1" } });
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith({ llm_timeout_seconds: 300, llm_keep_alive_minutes: -1 }),
    );
  });

  it("blocks Save and explains an out-of-range value", async () => {
    render(<AgentConfigCard canEdit />);
    await screen.findByLabelText(/llm request timeout/i);

    fireEvent.change(timeoutInput(), { target: { value: "5" } });
    expect(screen.getByText("Enter whole seconds from 30 to 900.")).toBeTruthy();
    expect(saveButton().disabled).toBe(true);

    fireEvent.change(timeoutInput(), { target: { value: "180" } });
    fireEvent.change(keepAliveInput(), { target: { value: "500" } });
    expect(screen.getByText(/enter whole minutes from -1 to 240/i)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("still saves the watchlist/mode when the API predates these fields (frontend-first deploy)", async () => {
    const older: Partial<AgentConfig> = { ...config() };
    delete older.llm_timeout_seconds;
    delete older.llm_keep_alive_minutes;
    delete older.llm_timeout_default_seconds;
    delete older.llm_keep_alive_default_minutes;
    mockGetConfig.mockResolvedValue(older as AgentConfig);
    mockUpdate.mockResolvedValue(older as AgentConfig);
    render(<AgentConfigCard canEdit />);
    await screen.findByText("AAPL");

    expect(screen.queryByLabelText(/llm request timeout/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/approval mode/i), { target: { value: "OBSERVE_ONLY" } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ approval_mode: "OBSERVE_ONLY" }));
  });

  it("does not offer Save for input that equals the saved value (e.g. 0180)", async () => {
    render(<AgentConfigCard canEdit />);
    await screen.findByLabelText(/llm request timeout/i);

    fireEvent.change(timeoutInput(), { target: { value: "0180" } });

    expect(saveButton().disabled).toBe(true);
  });

  it("keeps the fields intact after saving a strategy", async () => {
    // An API from before #214 answered PUT /agent/{id}/strategy with the base config only
    // (no watchlist / #203 fields); the card must still keep what it holds.
    const full = config({ strategy_version: "S-002" });
    const base: Partial<AgentConfig> = { ...full };
    delete base.watchlist;
    delete base.llm_timeout_seconds;
    delete base.llm_keep_alive_minutes;
    mockUpdateStrategy.mockResolvedValue(base as AgentConfig);
    render(<AgentConfigCard canEdit />);
    const strategy = (await screen.findByLabelText(/^strategy$/i)) as HTMLSelectElement;

    fireEvent.change(strategy, { target: { value: "S-002" } });
    fireEvent.click(screen.getByRole("button", { name: /^save strategy$/i }));

    await waitFor(() => expect(mockUpdateStrategy).toHaveBeenCalled());
    await waitFor(() => expect(timeoutInput().value).toBe("180"));
    expect(keepAliveInput().value).toBe("10");
    expect(screen.queryByText(/enter whole seconds/i)).toBeNull();
    expect(screen.getByText("AAPL")).toBeTruthy();
  });

  it("is read-only for a VIEWER", async () => {
    render(<AgentConfigCard canEdit={false} />);

    expect((await screen.findByLabelText(/llm request timeout/i) as HTMLInputElement).disabled).toBe(true);
    expect(keepAliveInput().disabled).toBe(true);
  });

  it("hides keep-alive and explains why when the provider isn't Ollama", async () => {
    mockLlmConfig.mockResolvedValue({ provider: "openai", model: "gpt-4o-mini" });
    render(<AgentConfigCard canEdit />);

    expect(await screen.findByText(/apply only to a local ollama model/i)).toBeTruthy();
    expect(screen.queryByLabelText(/model keep-alive/i)).toBeNull();
    expect(timeoutInput().disabled).toBe(true);
  });
});
