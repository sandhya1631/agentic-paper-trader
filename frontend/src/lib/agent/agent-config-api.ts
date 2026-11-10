/**
 * Agent configuration (#141): read and update the caller's own agent. The shared api
 * client attaches the Bearer token and handles 401 globally. This only changes what the
 * Context/Reasoning layers read on the next cycle; it never places orders.
 */
import { api } from "@/lib/api/client";

/** `GET/PATCH /agent/config` response. Decimal fields arrive as strings (fractions). */
export interface AgentConfig {
  id: string;
  name: string;
  strategy_version: string;
  /** Display only: scheduling uses the Autonomous schedule interval, not this value. */
  cadence_minutes: number;
  target_allocation: string;
  max_symbol_allocation: string;
  cash_reserve: string;
  daily_loss_limit: string;
  /** One of `/meta/constants` `approval_mode` (OBSERVE_ONLY / REQUIRED / AUTO). */
  approval_mode: string;
  enabled: boolean;
  watchlist: string[];
}

/** Only the editable fields; send just the ones that changed (the server rejects `{}`). */
export interface AgentConfigUpdate {
  watchlist?: string[];
  approval_mode?: string;
}

export function getAgentConfig(): Promise<AgentConfig> {
  return api.get<AgentConfig>("/agent/config");
}

export function updateAgentConfig(update: AgentConfigUpdate): Promise<AgentConfig> {
  return api.patch<AgentConfig>("/agent/config", update);
}
