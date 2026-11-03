import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import {
  REJECT_MESSAGE,
  describeApprovalError,
  describeCycleOutcome,
  formatCountdown,
  formatEvidence,
  isExpired,
  outcomeStyle,
  parsePolicyResult,
  parseUtc,
} from "./approval-view";

const NOW = Date.parse("2026-10-30T12:00:00Z");

function apiError(status: number, detail?: string): ApiError {
  return new ApiError(status, "Error", detail ? { detail } : null, "http://api/test");
}

const policyResult = {
  decision: "ALLOW",
  policy_version: "POLICY-1.0",
  evaluated_at: "2026-10-30T11:59:00Z",
  evaluations: [
    {
      policy_id: "P-001",
      policy_name: "Valid proposal",
      outcome: "PASS",
      reason: "Quantity is positive",
      evidence: { quantity: 3 },
    },
    {
      policy_id: "P-003",
      policy_name: "Market open",
      outcome: "FAIL",
      reason: "Market is closed",
      evidence: {},
    },
    {
      policy_id: "P-005",
      policy_name: "Position check",
      outcome: "NOT_APPLICABLE",
      reason: "Not a SELL",
    },
  ],
};

describe("parsePolicyResult", () => {
  it("reads each policy with PASS / FAIL / NOT_APPLICABLE and its evidence", () => {
    const parsed = parsePolicyResult(policyResult);

    expect(parsed?.decision).toBe("ALLOW");
    expect(parsed?.policy_version).toBe("POLICY-1.0");
    expect(parsed?.evaluations.map((e) => e.outcome)).toEqual(["PASS", "FAIL", "NOT_APPLICABLE"]);
    expect(parsed?.evaluations[0].evidence).toEqual({ quantity: 3 });
    expect(parsed?.evaluations[2].evidence).toEqual({});
  });

  it("returns null when the preview is missing or unusable", () => {
    expect(parsePolicyResult(null)).toBeNull();
    expect(parsePolicyResult("nope")).toBeNull();
    expect(parsePolicyResult({ decision: "ALLOW" })).toBeNull();
    expect(parsePolicyResult({ evaluations: [] })).toBeNull();
    expect(parsePolicyResult({ evaluations: [{ policy_id: 1 }] })).toBeNull();
  });

  it("skips malformed rows but keeps the valid ones", () => {
    const parsed = parsePolicyResult({
      evaluations: [{ nonsense: true }, policyResult.evaluations[0]],
    });

    expect(parsed?.evaluations).toHaveLength(1);
    expect(parsed?.decision).toBe("UNKNOWN");
  });
});

describe("outcomeStyle", () => {
  it("labels each outcome", () => {
    expect(outcomeStyle("PASS").label).toBe("PASS");
    expect(outcomeStyle("FAIL").label).toBe("FAIL");
    expect(outcomeStyle("NOT_APPLICABLE").label).toBe("N/A");
  });
});

describe("formatEvidence", () => {
  it("renders key: value lines and truncates long values", () => {
    const lines = formatEvidence({ price: 191.5, symbol: "AAPL", note: "x".repeat(200) });

    expect(lines[0]).toBe("price: 191.5");
    expect(lines[1]).toBe("symbol: AAPL");
    expect(lines[2].endsWith("...")).toBe(true);
    expect(lines[2].length).toBeLessThan(100);
  });
});

describe("expiry countdown", () => {
  it("formats the time left in the right unit", () => {
    expect(formatCountdown("2026-10-31T11:59:00Z", NOW)).toBe("23h 59m");
    expect(formatCountdown("2026-10-30T12:04:12Z", NOW)).toBe("4m 12s");
    expect(formatCountdown("2026-10-30T12:00:09Z", NOW)).toBe("9s");
  });

  it("shows Expired once the time has passed", () => {
    expect(formatCountdown("2026-10-30T12:00:00Z", NOW)).toBe("Expired");
    expect(formatCountdown("2026-10-30T11:00:00Z", NOW)).toBe("Expired");
    expect(isExpired("2026-10-30T11:59:59Z", NOW)).toBe(true);
    expect(isExpired("2026-10-30T12:00:01Z", NOW)).toBe(false);
  });

  it("treats timestamps without a timezone as UTC", () => {
    expect(parseUtc("2026-10-30T12:00:00")).toBe(NOW);
    expect(parseUtc("2026-10-30T12:00:00+00:00")).toBe(NOW);
  });
});

describe("describeCycleOutcome (after Approve)", () => {
  it("reports a submitted order", () => {
    const result = describeCycleOutcome({
      id: "c",
      status: "COMPLETED",
      failure_code: null,
      failure_message: null,
    });
    expect(result.tone).toBe("success");
    expect(result.message).toContain("submitted");
  });

  it("surfaces a final policy denial instead of hiding it", () => {
    const result = describeCycleOutcome({
      id: "c",
      status: "REJECTED",
      failure_code: "POLICY_DENIED",
      failure_message: null,
    });
    expect(result.tone).toBe("warning");
    expect(result.message).toContain("no order was sent");
  });

  it("reports a broker rejection with its reason", () => {
    const result = describeCycleOutcome({
      id: "c",
      status: "REJECTED",
      failure_code: "BROKER_REJECTED",
      failure_message: "insufficient buying power",
    });
    expect(result.tone).toBe("error");
    expect(result.message).toContain("insufficient buying power");
  });

  it("warns when the order status is uncertain", () => {
    const result = describeCycleOutcome({
      id: "c",
      status: "RECONCILING",
      failure_code: "ORDER_EXECUTION_UNCERTAIN",
      failure_message: null,
    });
    expect(result.tone).toBe("warning");
  });

  it("has a clear message for a rejection by the operator", () => {
    expect(REJECT_MESSAGE.message).toContain("No order was sent");
  });
});

describe("describeApprovalError", () => {
  it("explains an expired approval (409)", () => {
    expect(describeApprovalError(apiError(409, "Approval has expired"))).toContain("expired");
  });

  it("explains an approval that was already handled (409)", () => {
    expect(describeApprovalError(apiError(409, "Approval is no longer pending"))).toContain(
      "already handled",
    );
  });

  it("passes through other conflict reasons", () => {
    expect(describeApprovalError(apiError(409, "Cycle proposal no longer matches approval"))).toBe(
      "Conflict: Cycle proposal no longer matches approval",
    );
  });

  it("explains the kill switch block (423)", () => {
    expect(describeApprovalError(apiError(423))).toContain("kill switch");
  });

  it("explains an unconfirmed broker result (502)", () => {
    expect(describeApprovalError(apiError(502))).toContain("Check Positions");
  });

  it("explains a missing approval (404)", () => {
    expect(describeApprovalError(apiError(404, "Approval not found"))).toContain("no longer exists");
  });

  it("falls back to a generic message for unknown errors", () => {
    expect(describeApprovalError("weird")).toBe("Could not complete the action. Please try again.");
  });
});