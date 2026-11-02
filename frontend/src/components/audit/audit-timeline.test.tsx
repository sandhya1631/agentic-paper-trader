// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/cycles-api", () => ({ listCycles: vi.fn(), getCycle: vi.fn() }));

import { getCycle, listCycles, type AgentCycle } from "@/lib/agent/cycles-api";
import { AuditTimeline } from "./audit-timeline";

const mockList = vi.mocked(listCycles);
const mockGet = vi.mocked(getCycle);

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
});
