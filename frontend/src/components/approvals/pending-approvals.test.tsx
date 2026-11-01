// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

// Mock only the API seam; the view-model helpers stay real so these tests
// exercise the real wiring between the component and approval-view.ts.
vi.mock("@/lib/approvals/approvals-api", () => ({
  listPendingApprovals: vi.fn(),
  approveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}));

import {
  approveApproval,
  listPendingApprovals,
  rejectApproval,
  type PendingApproval,
} from "@/lib/approvals/approvals-api";
import { PendingApprovals } from "./pending-approvals";

const mockList = vi.mocked(listPendingApprovals);
const mockApprove = vi.mocked(approveApproval);
const mockReject = vi.mocked(rejectApproval);

const FAR_FUTURE = "2099-01-01T00:00:00Z";
const PAST = "2000-01-01T00:00:00Z";

function makePending(overrides: Partial<PendingApproval["approval"]> = {}): PendingApproval {
  return {
    approval: {
      id: "ap-1",
      cycle_id: "cy-1",
      status: "PENDING",
      requested_at: "2026-11-01T11:00:00Z",
      expires_at: FAR_FUTURE,
      acted_at: null,
      actor_id: null,
      comment: null,
      policy_version: "P-1",
      policy_result_json: {
        decision: "ALLOW",
        policy_version: "P-1",
        evaluations: [
          {
            policy_id: "P-001",
            policy_name: "Valid proposal",
            outcome: "PASS",
            reason: "Quantity is positive",
            evidence: { quantity: 3 },
          },
          {
            policy_id: "P-002",
            policy_name: "Exposure limit",
            outcome: "FAIL",
            reason: "Exceeds the 10% cap",
            evidence: { exposure: "12%" },
          },
        ],
      },
      ...overrides,
    },
    proposal: { symbol: "AAPL", action: "BUY", quantity: 3, reasoning: "Momentum breakout" },
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockApprove.mockReset();
  mockReject.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("PendingApprovals", () => {
  it("renders a pending proposal with its details and policy table (criteria 1, 3)", async () => {
    mockList.mockResolvedValue([makePending()]);

    render(<PendingApprovals />);

    expect(await screen.findByText("AAPL")).toBeTruthy();
    expect(screen.getByText("BUY")).toBeTruthy();
    expect(screen.getByText("3 shares")).toBeTruthy();
    expect(screen.getByText("Momentum breakout")).toBeTruthy();

    // Policy table: both the PASS and the FAIL row, with reason + evidence, must render.
    expect(screen.getByText("P-001")).toBeTruthy();
    expect(screen.getByText("PASS")).toBeTruthy();
    expect(screen.getByText("FAIL")).toBeTruthy();
    expect(screen.getByText("Exceeds the 10% cap")).toBeTruthy();
    expect(screen.getByText("exposure: 12%")).toBeTruthy();
  });

  it("shows a live countdown for a not-yet-expired approval (criterion 2)", async () => {
    mockList.mockResolvedValue([makePending()]);

    render(<PendingApprovals />);

    expect(await screen.findByText(/Expires in/)).toBeTruthy();
  });

  it("disables Approve and shows a message for an expired approval (criterion 5)", async () => {
    mockList.mockResolvedValue([makePending({ expires_at: PAST })]);

    render(<PendingApprovals />);

    const approve = await screen.findByRole("button", {
      name: /approve and place paper order/i,
    });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/this approval has expired and can no longer be approved/i),
    ).toBeTruthy();

    fireEvent.click(approve);
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("surfaces a submitted order after Approve (criterion 4)", async () => {
    mockList.mockResolvedValue([makePending()]);
    mockApprove.mockResolvedValue({
      id: "cy-1",
      status: "COMPLETED",
      failure_code: null,
      failure_message: null,
    });

    render(<PendingApprovals />);
    fireEvent.click(await screen.findByRole("button", { name: /approve and place paper order/i }));

    expect(await screen.findByText(/the paper order was submitted/i)).toBeTruthy();
    expect(mockApprove).toHaveBeenCalledWith("ap-1", undefined);
  });

  it("surfaces a final policy denial instead of implying success (criterion 6, safety)", async () => {
    mockList.mockResolvedValue([makePending()]);
    mockApprove.mockResolvedValue({
      id: "cy-1",
      status: "REJECTED",
      failure_code: "POLICY_DENIED",
      failure_message: null,
    });

    render(<PendingApprovals />);
    fireEvent.click(await screen.findByRole("button", { name: /approve and place paper order/i }));

    expect(await screen.findByText(/no order was sent/i)).toBeTruthy();
  });

  it("surfaces the kill-switch block (HTTP 423) distinctly (criterion 6)", async () => {
    mockList.mockResolvedValue([makePending()]);
    mockApprove.mockRejectedValue(new ApiError(423, "Locked", { detail: "kill switch" }, "http://x"));

    render(<PendingApprovals />);
    fireEvent.click(await screen.findByRole("button", { name: /approve and place paper order/i }));

    expect(await screen.findByText(/kill switch/i)).toBeTruthy();
  });

  it("passes the typed comment through to Reject (criterion 4)", async () => {
    mockList.mockResolvedValue([makePending()]);
    mockReject.mockResolvedValue({
      id: "cy-1",
      status: "REJECTED",
      failure_code: null,
      failure_message: null,
    });

    render(<PendingApprovals />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText(/comment/i), { target: { value: "not now" } });
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await waitFor(() => expect(mockReject).toHaveBeenCalledWith("ap-1", "not now"));
    expect(await screen.findByText(/proposal rejected/i)).toBeTruthy();
  });

  it("renders the empty state when nothing is pending", async () => {
    mockList.mockResolvedValue([]);

    render(<PendingApprovals />);

    expect(await screen.findByText(/no proposals are waiting for approval/i)).toBeTruthy();
  });

  it("renders a load error distinctly when the list request fails", async () => {
    mockList.mockRejectedValue(new ApiError(500, "Server Error", null, "http://x"));

    render(<PendingApprovals />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBeTruthy();
  });
});
