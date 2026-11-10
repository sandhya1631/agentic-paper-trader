// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/controls-api", () => ({
  getCurrentUser: vi.fn(),
  triggerCycle: vi.fn(),
  getKillSwitch: vi.fn(),
  setKillSwitch: vi.fn(),
  getKillSwitchEvents: vi.fn(),
}));
vi.mock("@/lib/accounts/accounts-api", () => ({
  getAccountStatus: vi.fn(),
  connectAccount: vi.fn(),
  disconnectAccount: vi.fn(),
}));
vi.mock("@/lib/agent/scheduler-api", () => ({
  getSchedulerStatus: vi.fn(),
  startSchedule: vi.fn(),
  stopSchedule: vi.fn(),
}));
vi.mock("@/lib/agent/cycles-api", () => ({ listCycles: vi.fn() }));
vi.mock("@/lib/agent/observability-api", () => ({ getSchedulerLogs: vi.fn() }));
vi.mock("@/lib/agent/agent-config-api", () => ({
  getAgentConfig: vi.fn(),
  updateAgentConfig: vi.fn(),
}));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));

import {
  getCurrentUser,
  getKillSwitch,
  getKillSwitchEvents,
  setKillSwitch,
  triggerCycle,
} from "@/lib/agent/controls-api";
import { getSchedulerLogs } from "@/lib/agent/observability-api";
import {
  connectAccount,
  disconnectAccount,
  getAccountStatus,
} from "@/lib/accounts/accounts-api";
import { getSchedulerStatus, startSchedule, stopSchedule } from "@/lib/agent/scheduler-api";
import { listCycles } from "@/lib/agent/cycles-api";
import { getAgentConfig } from "@/lib/agent/agent-config-api";
import { getConstants } from "@/lib/meta/meta-api";
import { SettingsView } from "./settings-view";

const mockMe = vi.mocked(getCurrentUser);
const mockTrigger = vi.mocked(triggerCycle);
const mockGetKs = vi.mocked(getKillSwitch);
const mockSetKs = vi.mocked(setKillSwitch);
const mockGetKsEvents = vi.mocked(getKillSwitchEvents);
const mockAccountStatus = vi.mocked(getAccountStatus);
const mockConnect = vi.mocked(connectAccount);
const mockDisconnect = vi.mocked(disconnectAccount);
const mockSchedStatus = vi.mocked(getSchedulerStatus);
const mockStart = vi.mocked(startSchedule);
const mockStop = vi.mocked(stopSchedule);
const mockCycles = vi.mocked(listCycles);
const mockLogs = vi.mocked(getSchedulerLogs);
const mockAgentConfig = vi.mocked(getAgentConfig);
const mockConstants = vi.mocked(getConstants);

function notFound() {
  return new ApiError(404, "Not Found", { detail: "No Alpaca account connected" }, "http://x");
}

function cycle(overrides: Record<string, unknown> = {}) {
  return {
    id: "c1",
    agent_id: "a1",
    trigger: "MANUAL",
    status: "COMPLETED",
    started_at: "2026-11-02T01:00:00Z",
    completed_at: null,
    failure_code: null,
    failure_message: null,
    snapshot_json: { execution_status: "ORDER_SUBMITTED" },
    prompt_version: "S-001",
    prompt_revision: "1.0.0",
    prompt_text: null,
    llm_raw_response: null,
    proposal_json: { symbol: "AAPL", action: "BUY", quantity: 1, reasoning: "x" },
    policy_decision: "ALLOW",
    policy_result_json: null,
    correlation_id: null,
    approval: null,
    ...overrides,
  } as never;
}

function schedStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false,
    running: false,
    interval_minutes: null,
    next_run_time: null,
    last_tick: null,
    schedules: [],
    recent_cycles: [],
    ...overrides,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  // Safe defaults so every card mounts without unhandled rejections.
  mockMe.mockResolvedValue({ id: "u1", email: "v@example.com", role: "VIEWER" });
  mockGetKs.mockResolvedValue({
    enabled: false,
    enforced_by_environment: false,
    updated_at: null,
    updated_by: null,
  });
  mockAccountStatus.mockRejectedValue(notFound());
  mockSchedStatus.mockResolvedValue(schedStatus());
  mockCycles.mockResolvedValue({ items: [], total: 0, limit: 1, offset: 0, next_offset: null });
  mockLogs.mockResolvedValue([]);
  mockGetKsEvents.mockResolvedValue([]);
  mockAgentConfig.mockResolvedValue({
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
  });
  mockConstants.mockResolvedValue({
    approval_mode: ["OBSERVE_ONLY", "REQUIRED", "AUTO"],
    max_watchlist_size: 20,
  } as never);
});
afterEach(() => cleanup());

describe("Agent configuration gating", () => {
  it("is read-only for viewers", async () => {
    render(<SettingsView />);
    const remove = (await screen.findByRole("button", { name: /remove aapl/i })) as HTMLButtonElement;
    expect(remove.disabled).toBe(true);
    expect(screen.getByText(/only operators and admins/i)).toBeTruthy();
  });

  it("is editable for operators", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "o@example.com", role: "OPERATOR" });
    render(<SettingsView />);
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: /remove aapl/i }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(screen.queryByText(/only operators and admins/i)).toBeNull();
  });
});

describe("Trigger Now", () => {
  it("runs a cycle and shows the result", async () => {
    mockTrigger.mockResolvedValue(cycle());
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /trigger now/i }));
    expect(await screen.findByText("COMPLETED")).toBeTruthy();
    expect(screen.getByText(/BUY AAPL/)).toBeTruthy();
  });

  it("explains a 409 non-destructively", async () => {
    mockTrigger.mockRejectedValue(new ApiError(409, "Conflict", { detail: "running" }, "http://x"));
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /trigger now/i }));
    expect(await screen.findByText(/a cycle is already running/i)).toBeTruthy();
  });
});

describe("Kill switch gating", () => {
  it("shows an admin-only note for non-admins", async () => {
    render(<SettingsView />);
    expect(await screen.findByText(/available to administrators only/i)).toBeTruthy();
    expect(mockGetKs).not.toHaveBeenCalled();
  });

  it("lets an admin enable it via confirmation", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockSetKs.mockResolvedValue({
      enabled: true,
      enforced_by_environment: false,
      updated_at: null,
      updated_by: "u1",
    });
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /enable kill switch/i }));
    fireEvent.change(await screen.findByLabelText(/reason/i), {
      target: { value: "Unexpected trades" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /^confirm$/i }));
    await waitFor(() => expect(mockSetKs).toHaveBeenCalledWith(true, "Unexpected trades"));
    expect(await screen.findByText("ENABLED")).toBeTruthy();
  });

  it("will not enable without a reason", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /enable kill switch/i }));

    const confirm = (await screen.findByRole("button", { name: /^confirm$/i })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: "   " } });
    expect(confirm.disabled).toBe(true);
    expect(mockSetKs).not.toHaveBeenCalled();
  });

  it("disables the kill switch without asking for a reason", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKs.mockResolvedValue({
      enabled: true,
      enforced_by_environment: false,
      updated_at: null,
      updated_by: "u1",
      reason: "Unexpected trades",
    });
    mockSetKs.mockResolvedValue({
      enabled: false,
      enforced_by_environment: false,
      updated_at: null,
      updated_by: "u1",
    });
    render(<SettingsView />);

    expect(await screen.findByText("Unexpected trades")).toBeTruthy(); // current reason shown
    fireEvent.click(await screen.findByRole("button", { name: /disable kill switch/i }));
    expect(screen.queryByLabelText(/reason/i)).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /^confirm$/i }));

    await waitFor(() => expect(mockSetKs).toHaveBeenCalledWith(false, ""));
  });
});

describe("Account connection", () => {
  it("submits entered keys to connect", async () => {
    mockConnect.mockResolvedValue({
      id: "acc1",
      provider: "ALPACA",
      is_paper: true,
      is_connected: true,
      last_verified_at: null,
    });
    render(<SettingsView />);

    fireEvent.change(await screen.findByLabelText(/api key/i), { target: { value: "KEY123" } });
    fireEvent.change(screen.getByLabelText(/api secret/i), { target: { value: "SECRET123" } });
    fireEvent.click(screen.getByRole("button", { name: /connect account/i }));

    await waitFor(() =>
      expect(mockConnect).toHaveBeenCalledWith({
        api_key: "KEY123",
        api_secret: "SECRET123",
        is_paper: true,
      }),
    );
  });

  it("shows connected state and disconnects", async () => {
    mockAccountStatus.mockResolvedValue({
      id: "acc1",
      provider: "ALPACA",
      is_paper: true,
      is_connected: true,
      last_verified_at: null,
    });
    mockDisconnect.mockResolvedValue(undefined);
    render(<SettingsView />);

    expect(await screen.findByText("Connected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /disconnect/i }));
    await waitFor(() => expect(mockDisconnect).toHaveBeenCalled());
  });
});

describe("Scheduler controls", () => {
  it("starts a schedule using the user's agent id", async () => {
    mockCycles.mockResolvedValue({
      items: [cycle({ id: "cx", agent_id: "agent-9" })],
      total: 1,
      limit: 1,
      offset: 0,
      next_offset: null,
    });
    mockStart.mockResolvedValue({
      schedule: { id: "s1", agent_id: "agent-9", interval_minutes: 15, enabled: true, next_run_time: null, last_tick: null },
      status_url: "/agent/scheduler/status",
    });
    render(<SettingsView />);

    fireEvent.click(await screen.findByRole("button", { name: /start schedule/i }));
    await waitFor(() => expect(mockStart).toHaveBeenCalledWith("agent-9", 15));
  });

  it("stops an existing schedule", async () => {
    mockSchedStatus.mockResolvedValue(
      schedStatus({
        running: true,
        schedules: [
          { id: "s1", agent_id: "a1", interval_minutes: 15, enabled: true, next_run_time: null },
        ],
      }),
    );
    mockStop.mockResolvedValue({
      id: "s1",
      agent_id: "a1",
      interval_minutes: 15,
      enabled: false,
      next_run_time: null,
      last_tick: null,
    });
    render(<SettingsView />);

    fireEvent.click(await screen.findByRole("button", { name: /stop schedule/i }));
    await waitFor(() => expect(mockStop).toHaveBeenCalledWith("s1"));
  });
});

describe("Kill-switch history", () => {
  it("is not fetched or shown for non-admins", async () => {
    render(<SettingsView />);
    await screen.findByText(/administrators only/i);
    expect(mockGetKsEvents).not.toHaveBeenCalled();
    expect(screen.queryByText(/kill-switch history/i)).toBeNull();
  });

  it("shows GLOBAL and USER events, newest first as returned by the API", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKsEvents.mockResolvedValue([
      {
        id: "e2",
        occurred_at: "2026-11-05T19:13:42Z",
        actor_id: "admin-1",
        previous_enabled: false,
        new_enabled: true,
        outcome: "APPLIED",
        reason: "global halt drill",
        scope: "GLOBAL",
        target_user_id: null,
        actor_email: "admin@example.com",
        target_user_email: null,
      },
      {
        id: "e1",
        occurred_at: "2026-11-05T19:00:09Z",
        actor_id: "op-1",
        previous_enabled: false,
        new_enabled: true,
        outcome: "APPLIED",
        reason: null,
        scope: "USER",
        target_user_id: "op-1",
        actor_email: "operator@example.com",
        target_user_email: "operator@example.com",
      },
    ] as never);

    render(<SettingsView />);

    expect(await screen.findByText(/global halt drill/i)).toBeTruthy();
    expect(screen.getByText("admin@example.com")).toBeTruthy();
    expect(screen.getByText("operator@example.com")).toBeTruthy();
    expect(screen.getAllByText("GLOBAL")).toHaveLength(1);
    expect(screen.getAllByText("USER")).toHaveLength(1);
  });

  it("falls back to a neutral label when the actor has no email", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKsEvents.mockResolvedValue([
      {
        id: "e1",
        occurred_at: "2026-11-05T19:00:09Z",
        actor_id: null,
        previous_enabled: true,
        new_enabled: false,
        outcome: "APPLIED",
        reason: null,
        scope: "GLOBAL",
        target_user_id: null,
        actor_email: null,
        target_user_email: null,
      },
    ] as never);

    render(<SettingsView />);

    expect(await screen.findByText("System")).toBeTruthy();
  });

  it("shows an empty state when there is no history yet", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKsEvents.mockResolvedValue([]);

    render(<SettingsView />);

    expect(await screen.findByText(/no kill-switch changes yet/i)).toBeTruthy();
  });

  it("shows an error state when the history fails to load", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKsEvents.mockRejectedValue(
      new ApiError(403, "Forbidden", { detail: "Admin role required" }, "http://x"),
    );

    render(<SettingsView />);

    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});