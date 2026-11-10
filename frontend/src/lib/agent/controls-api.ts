/**
 * Operator controls: current user (for role gating), manual cycle trigger, and the
 * admin-only kill switch. The shared api client attaches the Bearer token / handles 401.
 */
import { api } from "@/lib/api/client";
import type { AgentCycle } from "@/lib/agent/cycles-api";

export type UserRole = "VIEWER" | "OPERATOR" | "ADMIN" | "AUDITOR";

export interface CurrentUser {
  id: string;
  email: string;
  role: UserRole;
}

export interface KillSwitch {
  enabled: boolean;
  enforced_by_environment: boolean;
  updated_at: string | null;
  updated_by: string | null;
  /** Why it was last turned on (shown while it is on). */
  reason?: string | null;
}

export function getCurrentUser(): Promise<CurrentUser> {
  return api.get<CurrentUser>("/auth/me");
}

/** Fire an on-demand agent cycle for the current user (auto-provisions a default agent). */
export function triggerCycle(): Promise<AgentCycle> {
  return api.post<AgentCycle>("/agent/cycles/run");
}

export function getKillSwitch(): Promise<KillSwitch> {
  return api.get<KillSwitch>("/agent/kill-switch");
}

/** Turning the switch ON needs a reason (the server rejects an empty one with 422). */
export function setKillSwitch(enabled: boolean, reason?: string): Promise<KillSwitch> {
  const trimmed = reason?.trim();
  return api.put<KillSwitch>("/agent/kill-switch", trimmed ? { enabled, reason: trimmed } : { enabled });
}

export type KillSwitchEventScope = "GLOBAL" | "USER";

export interface KillSwitchEvent {
  id: string;
  occurred_at: string;
  actor_id: string | null;
  previous_enabled: boolean;
  new_enabled: boolean;
  outcome: string;
  reason: string | null;
  /** GLOBAL = admin halt, affects everyone. USER = one user pausing their own scheduler. */
  scope: KillSwitchEventScope;
  target_user_id: string | null;
  actor_email: string | null;
  target_user_email: string | null;
}

/** Admin-only audit trail of every kill-switch change, newest first (#169). */
export function getKillSwitchEvents(limit = 50): Promise<KillSwitchEvent[]> {
  return api.get<KillSwitchEvent[]>(`/agent/kill-switch/events?limit=${limit}`);
}