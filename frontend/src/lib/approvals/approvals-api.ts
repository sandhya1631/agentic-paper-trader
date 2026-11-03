/**
 * Approvals API seam (Story #120).
 *
 * Thin, typed wrappers over the existing API client for the human-approval
 * endpoints. The client already attaches the Bearer token and handles 401.
 */
import { api } from "@/lib/api/client";

export type ApprovalStatus = "NOT_REQUIRED" | "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
export type PolicyOutcome = "PASS" | "FAIL" | "NOT_APPLICABLE";

export interface DecisionProposal {
  symbol: string;
  action: "BUY" | "SELL" | "HOLD";
  quantity: number;
  reasoning: string;
}

export interface ApprovalRecord {
  id: string;
  cycle_id: string;
  status: ApprovalStatus;
  requested_at: string;
  expires_at: string;
  acted_at: string | null;
  actor_id: string | null;
  comment: string | null;
  policy_version: string | null;
  /** Policy preview saved when the proposal was parked (see PolicyResult). */
  policy_result_json: Record<string, unknown> | null;
}

export interface PendingApproval {
  approval: ApprovalRecord;
  proposal: DecisionProposal;
}

/** The part of a cycle the approve/reject endpoints return that this screen uses. */
export interface CycleResult {
  id: string;
  status: string;
  failure_code: string | null;
  failure_message: string | null;
}

export function listPendingApprovals(): Promise<PendingApproval[]> {
  return api.get<PendingApproval[]>("/agent/approvals/pending");
}

function bodyWithComment(comment?: string): { comment?: string } {
  const trimmed = comment?.trim();
  return trimmed ? { comment: trimmed } : {};
}

/** Approve: the backend re-checks every policy before any order is sent. */
export function approveApproval(id: string, comment?: string): Promise<CycleResult> {
  return api.post<CycleResult>(`/agent/approvals/${id}/approve`, bodyWithComment(comment));
}

export function rejectApproval(id: string, comment?: string): Promise<CycleResult> {
  return api.post<CycleResult>(`/agent/approvals/${id}/reject`, bodyWithComment(comment));
}