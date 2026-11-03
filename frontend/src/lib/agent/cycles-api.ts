/**
 * Agent-cycle API seam. `listCycles` backs the dashboard history and the audit
 * timeline (#79/#81); `getCycle` fetches full detail on expand (#81). The shared
 * api client attaches the Bearer token and handles 401 globally.
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

export function listCycles(params: { limit?: number; offset?: number } = {}): Promise<CycleList> {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  const qs = query.toString();
  return api.get<CycleList>(`/agent/cycles${qs ? `?${qs}` : ""}`);
}

export function getCycle(id: string): Promise<AgentCycle> {
  return api.get<AgentCycle>(`/agent/cycles/${id}`);
}
