// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/broker/broker-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/broker/broker-api")>();
  return { ...actual, getAccount: vi.fn(), getPositions: vi.fn() };
});
vi.mock("@/lib/agent/cycles-api", () => ({ listCycles: vi.fn() }));
vi.mock("@/lib/agent/scheduler-api", () => ({ getSchedulerStatus: vi.fn() }));

import { getAccount, getPositions } from "@/lib/broker/broker-api";
import { listCycles } from "@/lib/agent/cycles-api";
import { getSchedulerStatus } from "@/lib/agent/scheduler-api";
import { DashboardView } from "./dashboard-view";

const mockAccount = vi.mocked(getAccount);
const mockPositions = vi.mocked(getPositions);
const mockCycles = vi.mocked(listCycles);
const mockScheduler = vi.mocked(getSchedulerStatus);

function account(overrides: Record<string, unknown> = {}) {
  return {
    id: "acct-1",
    status: "ACTIVE",
    currency: "USD",
    cash: "25000",
    portfolio_value: "100000",
    buying_power: "50000",
    equity: "100000",
    long_market_value: "0",
    short_market_value: "0",
    initial_margin: "0",
    maintenance_margin: "0",
    daytrading_buying_power: "0",
    regt_buying_power: "0",
    last_equity: "99000",
    is_paper: true,
    ...overrides,
  };
}

beforeEach(() => {
  mockAccount.mockReset();
  mockPositions.mockReset();
  mockCycles.mockReset();
  mockScheduler.mockReset();
  mockPositions.mockResolvedValue([]);
  mockCycles.mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0, next_offset: null });
  mockScheduler.mockResolvedValue({
    enabled: false,
    running: true,
    interval_minutes: 15,
    next_run_time: null,
    last_tick: null,
    schedules: [],
    recent_cycles: [],
  });
});

afterEach(() => cleanup());

describe("DashboardView", () => {
  it("renders live account cards and derived daily P/L", async () => {
    mockAccount.mockResolvedValue(account());

    render(<DashboardView />);

    expect(await screen.findByText("$100,000.00")).toBeTruthy(); // portfolio value
    expect(screen.getByText("$25,000.00")).toBeTruthy(); // cash
    expect(screen.getByText("$50,000.00")).toBeTruthy(); // buying power
    expect(screen.getByText("+$1,000.00")).toBeTruthy(); // daily P/L = equity - last_equity
    expect(screen.getByText("+1.01%")).toBeTruthy(); // 1000 / 99000
  });

  it("shows the connect-account notice when no account is connected", async () => {
    mockAccount.mockRejectedValue(
      new ApiError(503, "Service Unavailable", { detail: "No Alpaca account connected" }, "http://x"),
    );

    render(<DashboardView />);

    expect(await screen.findByText(/no alpaca paper account connected/i)).toBeTruthy();
  });

  it("surfaces a real account error distinctly (not the connect notice)", async () => {
    mockAccount.mockRejectedValue(new ApiError(500, "Server Error", { detail: "boom" }, "http://x"));

    render(<DashboardView />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("boom");
  });

  it("lists recent cycles and reflects an active schedule", async () => {
    mockAccount.mockResolvedValue(account());
    mockCycles.mockResolvedValue({
      items: [
        {
          id: "c1",
          agent_id: "a1",
          trigger: "MANUAL",
          status: "HOLD",
          started_at: "2026-11-02T01:00:00Z",
          completed_at: "2026-11-02T01:00:05Z",
          failure_code: null,
          failure_message: null,
          snapshot_json: null,
          prompt_version: "S-001",
          prompt_revision: "1.0.0",
          prompt_text: null,
          llm_raw_response: null,
          proposal_json: { symbol: "AAPL", action: "HOLD", quantity: 0, reasoning: "x" },
          policy_decision: "NOT_EVALUATED",
          policy_result_json: null,
          correlation_id: null,
          approval: null,
        },
      ],
      total: 1,
      limit: 5,
      offset: 0,
      next_offset: null,
    });
    mockScheduler.mockResolvedValue({
      enabled: true,
      running: true,
      interval_minutes: 15,
      next_run_time: "2026-11-02T02:00:00Z",
      last_tick: null,
      schedules: [
        { id: "s1", agent_id: "a1", interval_minutes: 15, enabled: true, next_run_time: "2026-11-02T02:00:00Z" },
      ],
      recent_cycles: [],
    });

    render(<DashboardView />);

    expect(await screen.findByText(/HOLD AAPL/)).toBeTruthy();
    expect(screen.getByText("Scheduled")).toBeTruthy();
  });
});
