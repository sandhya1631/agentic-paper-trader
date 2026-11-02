// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/controls-api", () => ({
  getCurrentUser: vi.fn(),
  triggerCycle: vi.fn(),
  getKillSwitch: vi.fn(),
  setKillSwitch: vi.fn(),
}));

import {
  getCurrentUser,
  getKillSwitch,
  setKillSwitch,
  triggerCycle,
} from "@/lib/agent/controls-api";
import { SettingsView } from "./settings-view";

const mockMe = vi.mocked(getCurrentUser);
const mockTrigger = vi.mocked(triggerCycle);
const mockGetKs = vi.mocked(getKillSwitch);
const mockSetKs = vi.mocked(setKillSwitch);

function cycle(overrides: Record<string, unknown> = {}) {
  return {
    id: "c1",
    agent_id: "a1",
    trigger: "MANUAL",
    status: "COMPLETED",
    started_at: "2026-11-02T01:00:00Z",
    completed_at: "2026-11-02T01:00:05Z",
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

beforeEach(() => {
  mockMe.mockReset();
  mockTrigger.mockReset();
  mockGetKs.mockReset();
  mockSetKs.mockReset();
  mockGetKs.mockResolvedValue({
    enabled: false,
    enforced_by_environment: false,
    updated_at: null,
    updated_by: null,
  });
});
afterEach(() => cleanup());

describe("Trigger Now", () => {
  it("runs a cycle and shows the result", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "v@example.com", role: "VIEWER" });
    mockTrigger.mockResolvedValue(cycle());

    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /trigger now/i }));

    expect(await screen.findByText("COMPLETED")).toBeTruthy();
    expect(screen.getByText(/BUY AAPL/)).toBeTruthy();
    expect(screen.getByText(/ORDER_SUBMITTED/)).toBeTruthy();
  });

  it("explains a 409 (cycle already running) non-destructively", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "v@example.com", role: "VIEWER" });
    mockTrigger.mockRejectedValue(new ApiError(409, "Conflict", { detail: "running" }, "http://x"));

    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /trigger now/i }));

    expect(await screen.findByText(/a cycle is already running/i)).toBeTruthy();
  });
});

describe("Kill switch gating", () => {
  it("is hidden behind an admin-only note for non-admins", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "v@example.com", role: "VIEWER" });

    render(<SettingsView />);

    expect(await screen.findByText(/available to administrators only/i)).toBeTruthy();
    expect(mockGetKs).not.toHaveBeenCalled();
  });

  it("lets an admin enable it via a confirmation step", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockSetKs.mockResolvedValue({
      enabled: true,
      enforced_by_environment: false,
      updated_at: null,
      updated_by: "u1",
    });

    render(<SettingsView />);

    fireEvent.click(await screen.findByRole("button", { name: /enable kill switch/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^confirm$/i }));

    await waitFor(() => expect(mockSetKs).toHaveBeenCalledWith(true));
    expect(await screen.findByText("ENABLED")).toBeTruthy();
  });

  it("disables the toggle and explains when env-forced-on", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@example.com", role: "ADMIN" });
    mockGetKs.mockResolvedValue({
      enabled: true,
      enforced_by_environment: true,
      updated_at: null,
      updated_by: null,
    });

    render(<SettingsView />);

    expect(await screen.findByText(/locked on by environment/i)).toBeTruthy();
    const btn = screen.getByRole("button", { name: /disable kill switch/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
