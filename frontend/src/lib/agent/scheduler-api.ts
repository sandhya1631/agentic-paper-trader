/**
 * Scheduler API seam. PR #79 reads status for the dashboard agent-state line;
 * PR #84 adds start/stop. The status endpoint is available to any authenticated
 * user (unlike the admin-only kill-switch).
 */
import { api } from "@/lib/api/client";

export interface SchedulerScheduleRow {
  id: string;
  agent_id: string;
  interval_minutes: number;
  enabled: boolean;
  next_run_time: string | null;
}

export interface SchedulerStatus {
  enabled: boolean;
  running: boolean;
  interval_minutes: number | null;
  next_run_time: string | null;
  last_tick: Record<string, unknown> | null;
  schedules: SchedulerScheduleRow[];
  recent_cycles: unknown[];
  trade_execution_note?: string;
}

export function getSchedulerStatus(): Promise<SchedulerStatus> {
  return api.get<SchedulerStatus>("/agent/scheduler/status");
}

export interface AgentScheduleRead {
  id: string;
  agent_id: string;
  interval_minutes: number;
  enabled: boolean;
  next_run_time: string | null;
  last_tick: Record<string, unknown> | null;
}

export interface ScheduleStartResult {
  schedule: AgentScheduleRead;
  status_url: string;
}

export function startSchedule(
  agentId: string,
  intervalMinutes: number,
): Promise<ScheduleStartResult> {
  return api.post<ScheduleStartResult>("/agent/scheduler/start", {
    agent_id: agentId,
    interval_minutes: intervalMinutes,
  });
}

export function stopSchedule(scheduleId: string): Promise<AgentScheduleRead> {
  return api.post<AgentScheduleRead>(`/agent/scheduler/${scheduleId}/stop`);
}
