/**
 * Pure view helpers for the approvals screen (Story #120).
 *
 * Kept free of React so they can be unit-tested in the node test environment.
 */
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";
import type { CycleResult, PolicyOutcome } from "./approvals-api";

export interface PolicyEvaluation {
  policy_id: string;
  policy_name: string;
  outcome: PolicyOutcome;
  reason: string;
  evidence: Record<string, unknown>;
}

export interface PolicyResult {
  decision: string;
  policy_version: string | null;
  evaluations: PolicyEvaluation[];
}

export type Tone = "success" | "warning" | "error";
export interface Feedback {
  tone: Tone;
  message: string;
}

const OUTCOMES: readonly string[] = ["PASS", "FAIL", "NOT_APPLICABLE"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Defensively read the saved policy preview. Returns null when it is missing or
 * unusable, so the UI can say "not available" instead of crashing.
 */
export function parsePolicyResult(raw: unknown): PolicyResult | null {
  if (!isRecord(raw) || !Array.isArray(raw.evaluations)) return null;

  const evaluations: PolicyEvaluation[] = [];
  for (const item of raw.evaluations) {
    if (
      isRecord(item) &&
      typeof item.policy_id === "string" &&
      typeof item.policy_name === "string" &&
      typeof item.outcome === "string" &&
      OUTCOMES.includes(item.outcome) &&
      typeof item.reason === "string"
    ) {
      evaluations.push({
        policy_id: item.policy_id,
        policy_name: item.policy_name,
        outcome: item.outcome as PolicyOutcome,
        reason: item.reason,
        evidence: isRecord(item.evidence) ? item.evidence : {},
      });
    }
  }
  if (evaluations.length === 0) return null;

  return {
    decision: typeof raw.decision === "string" ? raw.decision : "UNKNOWN",
    policy_version: typeof raw.policy_version === "string" ? raw.policy_version : null,
    evaluations,
  };
}

/** Label + Tailwind classes for a policy outcome badge (theme tokens only). */
export function outcomeStyle(outcome: PolicyOutcome): { label: string; className: string } {
  switch (outcome) {
    case "PASS":
      return { label: "PASS", className: "bg-positive/10 text-positive" };
    case "FAIL":
      return { label: "FAIL", className: "bg-negative/10 text-negative" };
    default:
      return { label: "N/A", className: "bg-surface-muted text-muted" };
  }
}

/** Evidence as short "key: value" lines (objects are JSON, long values truncated). */
export function formatEvidence(evidence: Record<string, unknown>): string[] {
  return Object.entries(evidence).map(([key, value]) => {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return `${key}: ${text.length > 80 ? `${text.slice(0, 77)}...` : text}`;
  });
}

/** The API sends UTC timestamps; treat ones without a timezone marker as UTC. */
export function parseUtc(value: string): number {
  const hasZone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  return Date.parse(hasZone ? value : `${value}Z`);
}

export function isExpired(expiresAt: string, nowMs: number): boolean {
  return parseUtc(expiresAt) <= nowMs;
}

export function formatCountdown(expiresAt: string, nowMs: number): string {
  const totalSeconds = Math.floor((parseUtc(expiresAt) - nowMs) / 1000);
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "Expired";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export const REJECT_MESSAGE: Feedback = {
  tone: "success",
  message: "Proposal rejected. No order was sent.",
};

/** What happened after the operator pressed Approve (the cycle the API returns). */
export function describeCycleOutcome(cycle: CycleResult): Feedback {
  if (cycle.status === "COMPLETED") {
    return { tone: "success", message: "Approved. The paper order was submitted." };
  }
  if (cycle.status === "REJECTED" && cycle.failure_code === "POLICY_DENIED") {
    return {
      tone: "warning",
      message: "Approved, but the final risk check failed, so no order was sent.",
    };
  }
  if (cycle.status === "REJECTED" && cycle.failure_code === "BROKER_REJECTED") {
    const why = cycle.failure_message ? `: ${cycle.failure_message}` : "";
    return { tone: "error", message: `The broker rejected the order${why}` };
  }
  if (cycle.status === "RECONCILING") {
    return {
      tone: "warning",
      message: "The order status is uncertain. Check Positions before trying again.",
    };
  }
  return { tone: "success", message: `Done. Cycle status: ${cycle.status}.` };
}

/** A readable message for a failed approve/reject call (never silent). */
export function describeApprovalError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    const text = typeof detail === "string" ? detail : "";
    switch (error.status) {
      case 404:
        return "This approval no longer exists.";
      case 409:
        if (/expired/i.test(text)) {
          return "This approval has expired and can no longer be approved.";
        }
        if (/no longer pending/i.test(text)) {
          return "This approval was already handled.";
        }
        return text ? `Conflict: ${text}` : "This approval can no longer be changed.";
      case 423:
        return "Blocked by the global kill switch. Nothing was sent to the broker.";
      case 502:
        return "The broker did not confirm the order. Check Positions before trying again.";
      default:
        break;
    }
  }
  return getErrorMessage(error, "Could not complete the action. Please try again.");
}