/**
 * Observability API seam (#143) — scheduler tick logs and LLM health. The shared
 * api client attaches the Bearer token and handles 401 globally.
 */
import { api } from "@/lib/api/client";

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
 * `/llm/health` is a cheap echo of the configured provider/model. A successful
 * response means the backend's LLM config is reachable; some deployments may also
 * include explicit `reachable` / `model_available` flags, which we honor.
 */
export interface LlmHealth {
  provider?: string;
  model?: string;
  reachable?: boolean;
  model_available?: boolean;
  detail?: string;
}

export function getLlmHealth(): Promise<LlmHealth> {
  return api.get<LlmHealth>("/llm/health");
}
