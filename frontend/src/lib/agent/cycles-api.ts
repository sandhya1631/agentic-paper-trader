/**
 * Agent-cycle API seam. `listCycles` backs the dashboard history and the audit
 * timeline (#79/#81); `getCycle` fetches full detail on expand (#81); `getCycleTokenUsage`
 * fetches the same cycle's persisted model-context measurements on expand (#195);
 * `cancelCycle` cancels an owned, still-running MANUAL cycle (#199, backend #192). The
 * shared api client attaches the Bearer token and handles 401 globally.
 */
import { api } from "@/lib/api/client";

export interface CycleProposal {
  symbol: string;
  action: string;
  quantity: number;
  reasoning: string;
}

export interface PolicyEvaluation {
  policy_id: string;
  policy_name: string;
  outcome: "PASS" | "FAIL" | "NOT_APPLICABLE";
  reason: string;
  evidence?: Record<string, unknown>;
}

export interface PolicyResult {
  decision: string;
  policy_version?: string | null;
  evaluations?: PolicyEvaluation[];
}

export interface CycleApproval {
  id: string;
  cycle_id: string;
  status: string;
  requested_at: string;
  expires_at: string;
  acted_at: string | null;
  actor_id: string | null;
  comment: string | null;
  policy_version: string | null;
  policy_result_json: Record<string, unknown> | null;
}

export interface AgentCycle {
  id: string;
  agent_id: string;
  trigger: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  snapshot_json: Record<string, unknown> | null;
  prompt_version: string;
  prompt_revision: string;
  prompt_text: string | null;
  llm_raw_response: string | null;
  proposal_json: CycleProposal | null;
  policy_decision: string | null;
  policy_result_json: PolicyResult | Record<string, unknown> | null;
  correlation_id: string | null;
  approval: CycleApproval | null;
}

export interface CycleList {
  items: AgentCycle[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
}

export interface CycleListFilters {
  limit?: number;
  offset?: number;
  /** Inclusive, ISO 8601. AND with startedBefore when both are set. */
  startedAfter?: string;
  startedBefore?: string;
  action?: "BUY" | "SELL" | "HOLD";
  status?: string;
  /** The cycle's overall outcome, not any single policy's result (#82). */
  policyDecision?: "ALLOW" | "DENY" | "RESIZE" | "NOT_EVALUATED";
}

export function listCycles(params: CycleListFilters = {}): Promise<CycleList> {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  if (params.startedAfter) query.set("started_after", params.startedAfter);
  if (params.startedBefore) query.set("started_before", params.startedBefore);
  if (params.action) query.set("action", params.action);
  if (params.status) query.set("status", params.status);
  if (params.policyDecision) query.set("policy_decision", params.policyDecision);
  const qs = query.toString();
  return api.get<CycleList>(`/agent/cycles${qs ? `?${qs}` : ""}`);
}

export function getCycle(id: string): Promise<AgentCycle> {
  return api.get<AgentCycle>(`/agent/cycles/${id}`);
}

/**
 * Statuses `POST /agent/cycles/{id}/cancel` accepts (backend #192). A `SCHEDULED` cycle,
 * or one already `EXECUTING`/`RECONCILING`/`PENDING_APPROVAL`/terminal, answers 409.
 */
export const CANCELLABLE_CYCLE_STATUSES: readonly string[] = [
  "PENDING",
  "COLLECTING",
  "REASONING",
  "VALIDATING",
];

/** Only an owned, still-running `MANUAL` cycle can be cancelled from here (#199). */
export function isCancellableCycle(cycle: Pick<AgentCycle, "trigger" | "status">): boolean {
  return cycle.trigger === "MANUAL" && CANCELLABLE_CYCLE_STATUSES.includes(cycle.status);
}

export function cancelCycle(id: string): Promise<AgentCycle> {
  return api.post<AgentCycle>(`/agent/cycles/${id}/cancel`);
}

/** `GET /agent/cycles/{id}/token-usage` response: one cycle's model-context budget. */
export interface CycleTokenUsage {
  cycle_id: string;
  estimated_input_tokens: number;
  model_window_tokens: number;
  remaining_context_tokens: number;
  context_over_limit: boolean;
  context_truncated: boolean;
  context_chars: number;
  system_prompt_chars: number;
  symbols: number;
  bars_used: number;
  account_data_status: string;
}

/**
 * 404 (not found/not owned) and 409 (the cycle never built model context, e.g. it failed
 * before context-building) are both expected, non-error outcomes for a caller that just
 * wants "is there token usage to show" — callers should treat a rejection as "none available"
 * rather than a blocking failure (#195).
 */
export function getCycleTokenUsage(id: string): Promise<CycleTokenUsage> {
  return api.get<CycleTokenUsage>(`/agent/cycles/${id}/token-usage`);
}
