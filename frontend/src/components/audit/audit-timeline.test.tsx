// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/cycles-api", () => ({ listCycles: vi.fn(), getCycle: vi.fn() }));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));

import { getCycle, listCycles, type AgentCycle } from "@/lib/agent/cycles-api";
import { getConstants } from "@/lib/meta/meta-api";
import { AuditTimeline } from "./audit-timeline";

const mockList = vi.mocked(listCycles);
const mockGet = vi.mocked(getCycle);
const mockConstants = vi.mocked(getConstants);

function baseCycle(overrides: Partial<AgentCycle> = {}): AgentCycle {
  return {
    id: "c1",
    agent_id: "a1",
    trigger: "MANUAL",
    status: "COMPLETED",
    started_at: "2026-11-02T01:00:00Z",
    completed_at: "2026-11-02T01:00:05Z",
    failure_code: null,
    failure_message: null,
    snapshot_json: null,
    prompt_version: "S-001",
    prompt_revision: "1.0.0",
    prompt_text: null,
    llm_raw_response: null,
    proposal_json: { symbol: "AAPL", action: "BUY", quantity: 1, reasoning: "momentum" },
    policy_decision: "ALLOW",
    policy_result_json: null,
    correlation_id: null,
    approval: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockGet.mockReset();
  mockConstants.mockReset();
  mockConstants.mockResolvedValue({
    currency: "USD",
    cycle_trigger: ["MANUAL", "SCHEDULED"],
    cycle_status: ["COMPLETED", "HOLD", "REJECTED", "FAILED"],
    decision_action: ["BUY", "SELL", "HOLD"],
    policy_decision_type: ["ALLOW", "DENY", "RESIZE", "NOT_EVALUATED"],
    approval_mode: ["AUTO", "REQUIRED", "OBSERVE_ONLY"],
    approval_status: ["PENDING", "APPROVED", "REJECTED"],
  });
});
afterEach(() => cleanup());

describe("AuditTimeline", () => {
  it("renders a row per cycle and the empty state when there are none", async () => {
    mockList.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
    render(<AuditTimeline />);
    expect(await screen.findByText(/no agent cycles recorded yet/i)).toBeTruthy();
  });

  it("shows a load error distinctly", async () => {
    mockList.mockRejectedValue(new ApiError(500, "Server Error", { detail: "nope" }, "http://x"));
    render(<AuditTimeline />);
    expect((await screen.findByRole("alert")).textContent).toContain("nope");
  });

  it("expands a cycle and renders indicators, policy PASS/FAIL table, approval and broker outcome", async () => {
    mockList.mockResolvedValue({ items: [baseCycle()], total: 1, limit: 20, offset: 0, next_offset: null });
    mockGet.mockResolvedValue(
      baseCycle({
        snapshot_json: {
          market: { AAPL: { latest_price: 335, rsi_14: 58.9, sma_20: 334, sma_50: 333 } },
          execution_status: "ORDER_SUBMITTED",
        },
        policy_result_json: {
          decision: "ALLOW",
          policy_version: "POLICY-1.0",
          evaluations: [
            { policy_id: "P-001", policy_name: "Valid proposal", outcome: "PASS", reason: "ok", evidence: { quantity: 1 } },
            { policy_id: "P-002", policy_name: "Exposure", outcome: "FAIL", reason: "too big", evidence: {} },
          ],
        },
        approval: {
          id: "ap1",
          cycle_id: "c1",
          status: "APPROVED",
          requested_at: "2026-11-02T01:00:00Z",
          expires_at: "2026-11-02T02:00:00Z",
          acted_at: "2026-11-02T01:01:00Z",
          actor_id: "u1",
          comment: "looks good",
          policy_version: "POLICY-1.0",
          policy_result_json: null,
        },
      }),
    );

    render(<AuditTimeline />);
    fireEvent.click(await screen.findByRole("button", { name: /BUY AAPL/ }));

    expect(await screen.findByText("P-001")).toBeTruthy();
    expect(screen.getByText("PASS")).toBeTruthy();
    expect(screen.getByText("FAIL")).toBeTruthy();
    expect(screen.getByText("too big")).toBeTruthy();
    expect(screen.getByText("ORDER_SUBMITTED")).toBeTruthy();
    expect(screen.getByText("APPROVED")).toBeTruthy();
    expect(screen.getByText(/58\.9/)).toBeTruthy(); // RSI indicator
  });

  it("falls back to the approval's policy results when the cycle field is empty", async () => {
    mockList.mockResolvedValue({
      items: [baseCycle({ id: "c4", status: "REJECTED" })],
      total: 1,
      limit: 20,
      offset: 0,
      next_offset: null,
    });
    mockGet.mockResolvedValue(
      baseCycle({
        id: "c4",
        status: "REJECTED",
        policy_result_json: null,
        approval: {
          id: "ap4",
          cycle_id: "c4",
          status: "REJECTED",
          requested_at: "2026-11-02T01:00:00Z",
          expires_at: "2026-11-02T02:00:00Z",
          acted_at: "2026-11-02T01:05:00Z",
          actor_id: "u1",
          comment: "risk policies denied this proposal",
          policy_version: "POLICY-1.0",
          policy_result_json: {
            decision: "DENY",
            policy_version: "POLICY-1.0",
            evaluations: [
              { policy_id: "P-001", policy_name: "Paper-only", outcome: "PASS", reason: "ok", evidence: {} },
              {
                policy_id: "P-004",
                policy_name: "Fresh data",
                outcome: "FAIL",
                reason: "Market data is stale",
                evidence: { age_seconds: 2400000, max_seconds: 600 },
              },
            ],
          },
        },
      }),
    );

    render(<AuditTimeline />);
    fireEvent.click(await screen.findByRole("button", { name: /BUY AAPL/ }));

    expect(await screen.findByText("P-004")).toBeTruthy();
    expect(screen.getByText("PASS")).toBeTruthy();
    expect(screen.getByText("FAIL")).toBeTruthy();
    expect(screen.getByText("Market data is stale")).toBeTruthy();
    expect(screen.getByText(/from the approval re-check/i)).toBeTruthy();
    expect(screen.queryByText(/policies were not evaluated for this cycle/i)).toBeNull();
  });

  it("renders 'not evaluated' for a HOLD cycle without policy results", async () => {
    mockList.mockResolvedValue({
      items: [baseCycle({ id: "c2", status: "HOLD", policy_decision: "NOT_EVALUATED" })],
      total: 1,
      limit: 20,
      offset: 0,
      next_offset: null,
    });
    mockGet.mockResolvedValue(
      baseCycle({
        id: "c2",
        status: "HOLD",
        policy_decision: "NOT_EVALUATED",
        policy_result_json: null,
        snapshot_json: { execution_status: "NO_TRADE_PROPOSED" },
      }),
    );

    render(<AuditTimeline />);
    fireEvent.click(await screen.findByRole("button", { name: /BUY AAPL/ }));

    expect(await screen.findByText(/policies were not evaluated for this cycle/i)).toBeTruthy();
  });

  it("shows the failure reason for a FAILED cycle", async () => {
    mockList.mockResolvedValue({
      items: [baseCycle({ id: "c3", status: "FAILED" })],
      total: 1,
      limit: 20,
      offset: 0,
      next_offset: null,
    });
    mockGet.mockResolvedValue(
      baseCycle({
        id: "c3",
        status: "FAILED",
        failure_code: "LLM_OUTPUT_INVALID",
        failure_message: "bad json",
        proposal_json: null,
      }),
    );

    render(<AuditTimeline />);
    fireEvent.click(await screen.findByRole("button", { name: /FAILED/ }));

    expect(await screen.findByText(/LLM_OUTPUT_INVALID: bad json/)).toBeTruthy();
  });

  it("populates the action/status/policy dropdowns from /meta/constants", async () => {
    mockList.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
    render(<AuditTimeline />);

    await screen.findByText(/no agent cycles recorded yet/i);
    const actionSelect = screen.getByLabelText(/^action$/i) as HTMLSelectElement;
    const statusSelect = screen.getByLabelText(/^status$/i) as HTMLSelectElement;
    const policySelect = screen.getByLabelText(/policy outcome/i) as HTMLSelectElement;

    expect(Array.from(actionSelect.options).map((o) => o.value)).toEqual([
      "", "BUY", "SELL", "HOLD",
    ]);
    expect(Array.from(statusSelect.options).map((o) => o.value)).toEqual([
      "", "COMPLETED", "HOLD", "REJECTED", "FAILED",
    ]);
    expect(Array.from(policySelect.options).map((o) => o.value)).toEqual([
      "", "ALLOW", "DENY", "RESIZE", "NOT_EVALUATED",
    ]);
  });

  it("refetches with the selected filters and shows a matching-count line", async () => {
    mockList.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
    render(<AuditTimeline />);
    await screen.findByText(/no agent cycles recorded yet/i);
    mockList.mockClear();

    mockList.mockResolvedValue({
      items: [baseCycle()],
      total: 3,
      limit: 20,
      offset: 0,
      next_offset: null,
    });
    fireEvent.change(screen.getByLabelText(/^action$/i), { target: { value: "BUY" } });

    expect(await screen.findByText(/3 matching cycles/i)).toBeTruthy();
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 20, offset: 0, action: "BUY" }),
    );
  });

  it("AND-combines multiple filters into one request", async () => {
    mockList.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
    render(<AuditTimeline />);
    await screen.findByText(/no agent cycles recorded yet/i);
    mockList.mockClear();

    fireEvent.change(screen.getByLabelText(/^action$/i), { target: { value: "BUY" } });
    fireEvent.change(screen.getByLabelText(/^status$/i), { target: { value: "FAILED" } });
    fireEvent.change(screen.getByLabelText(/policy outcome/i), { target: { value: "DENY" } });
    fireEvent.change(screen.getByLabelText(/^from$/i), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText(/^to$/i), { target: { value: "2026-11-05" } });

    await screen.findByRole("button", { name: /clear filters/i });
    expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({
        action: "BUY",
        status: "FAILED",
        policyDecision: "DENY",
        startedAfter: "2026-11-01T00:00:00.000Z",
        startedBefore: "2026-11-05T23:59:59.999Z",
      }),
    );
  });

  it("clears all filters and refetches the unfiltered list", async () => {
    mockList.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
    render(<AuditTimeline />);
    await screen.findByText(/no agent cycles recorded yet/i);

    fireEvent.change(screen.getByLabelText(/^action$/i), { target: { value: "BUY" } });
    const clearButton = await screen.findByRole("button", { name: /clear filters/i });
    mockList.mockClear();

    fireEvent.click(clearButton);

    expect(await screen.findByText(/no agent cycles recorded yet/i)).toBeTruthy();
    expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({
        limit: 20,
        offset: 0,
        startedAfter: undefined,
        startedBefore: undefined,
        action: undefined,
        status: undefined,
        policyDecision: undefined,
      }),
    );
    expect(screen.queryByRole("button", { name: /clear filters/i })).toBeNull();
  });
});
