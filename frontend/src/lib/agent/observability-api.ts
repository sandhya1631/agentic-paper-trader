/**
 * Observability API seam (#143) — scheduler tick logs and LLM health. The shared
 * api client attaches the Bearer token and handles 401 globally.
 */
import { api } from "@/lib/api/client";
import { llmHealthFromProbeError } from "./scheduler-log-view";

/** One scheduler tick's outcome. All fields optional — older ticks may omit some. */
export interface SchedulerTick {
  status?: string; // COMPLETED | SKIPPED | FAILED | PARTIAL
  reason?: string | null;
  started_at?: string;
  completed_at?: string;
  enabled_agents?: number;
  attempted_agents?: number;
  successful_agents?: number;
  failed_agents?: number;
  overlapping_agents?: number;
}

export interface SchedulerTickLog {
  schedule_id: string | null;
  tick: SchedulerTick;
}

interface SchedulerLogsResponse {
  logs: SchedulerTickLog[];
}

/** Recent scheduler ticks (newest first). Empty when no ticks have run yet. */
export async function getSchedulerLogs(): Promise<SchedulerTickLog[]> {
  const res = await api.get<SchedulerLogsResponse>("/agent/scheduler/logs");
  return res?.logs ?? [];
}

/**
 * LLM health from `/llm/health?probe=true`: the backend actually contacts the
 * configured provider and reports `reachable` / `model_available`. Without `probe`
 * the endpoint only echoes the configured provider/model, so it would say nothing
 * about whether the LLM can be reached.
 */
export interface LlmHealth {
  provider?: string;
  model?: string;
  reachable?: boolean;
  model_available?: boolean;
  detail?: string;
}

/**
 * Probe the LLM. A 503 carrying `reachable: false` (LLM down or misconfigured) resolves
 * to that payload so callers can show the backend's reason; other failures reject.
 */
export async function getLlmHealth(): Promise<LlmHealth> {
  try {
    return await api.get<LlmHealth>("/llm/health?probe=true");
  } catch (error) {
    const reported = llmHealthFromProbeError(error);
    if (reported) return reported;
    throw error;
  }
}
