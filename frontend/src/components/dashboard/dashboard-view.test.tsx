// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

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

type Account = Awaited<ReturnType<typeof getAccount>>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Fire the 15 s auto-refresh. */
async function advancePoll() {
  await act(async () => {
    vi.advanceTimersByTime(15_000);
  });
}

describe("DashboardView live refresh in place (#164)", () => {
  beforeEach(() => {
    // Only the poll interval and the clock are faked; RTL's findBy* keeps real timeouts.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date(2026, 10, 9, 14, 41, 5));
    mockAccount.mockResolvedValue(account() as Account);
  });
  afterEach(() => vi.useRealTimers());

  it("shows the placeholder only on the first load", async () => {
    const first = deferred<Account>();
    mockAccount.mockReturnValueOnce(first.promise);
    render(<DashboardView />);
    expect(screen.getByText("Loading dashboard...")).toBeTruthy();

    await act(async () => first.resolve(account() as Account));
    expect(await screen.findByText("$100,000.00")).toBeTruthy();
    expect(screen.queryByText("Loading dashboard...")).toBeNull();
  });

  it("keeps the content on screen while a refresh is pending, then updates in place", async () => {
    render(<DashboardView />);
    expect(await screen.findByText("$100,000.00")).toBeTruthy();

    const refresh = deferred<Account>();
    mockAccount.mockReturnValueOnce(refresh.promise);
    await advancePoll();

    expect(mockAccount).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Loading dashboard/)).toBeNull();
    expect(screen.getByText("$100,000.00")).toBeTruthy();
    expect(screen.getByText("Recent decisions")).toBeTruthy();

    await act(async () =>
      refresh.resolve(account({ portfolio_value: "101500", cash: "26000" }) as Account),
    );
    expect(await screen.findByText("$101,500.00")).toBeTruthy();
    expect(screen.getByText("$26,000.00")).toBeTruthy();
    expect(screen.queryByText("$25,000.00")).toBeNull();
  });

  it("keeps the last good data with a non-blocking note when a refresh fails", async () => {
    render(<DashboardView />);
    await screen.findByText("$100,000.00");

    vi.setSystemTime(new Date(2026, 10, 9, 14, 45, 0));
    mockAccount.mockRejectedValueOnce(new ApiError(500, "Server Error", { detail: "boom" }, "http://x"));
    mockCycles.mockRejectedValueOnce(new ApiError(500, "Server Error", null, "http://x"));
    await advancePoll();

    const note = await screen.findByText(/couldn't refresh/i);
    expect(note.textContent).toMatch(/showing data from 2:41:05/);
    expect(screen.getByText("$100,000.00")).toBeTruthy();
    expect(screen.getByText("+$1,000.00")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull(); // not the account error block
    // The cycle list keeps its last good (empty) answer instead of "unavailable".
    expect(screen.getByText(/no agent cycles yet/i)).toBeTruthy();

    mockAccount.mockResolvedValueOnce(account({ portfolio_value: "101500" }) as Account);
    await advancePoll();
    expect(await screen.findByText("$101,500.00")).toBeTruthy();
    expect(screen.queryByText(/couldn't refresh/i)).toBeNull();
  });

  it("switches to the connect notice if the account is disconnected", async () => {
    render(<DashboardView />);
    await screen.findByText("$100,000.00");

    mockAccount.mockRejectedValueOnce(
      new ApiError(503, "Service Unavailable", { detail: "No Alpaca account connected" }, "http://x"),
    );
    await advancePoll();

    expect(await screen.findByText(/no alpaca paper account connected/i)).toBeTruthy();
    expect(screen.queryByText("$100,000.00")).toBeNull();
  });
});
