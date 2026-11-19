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
  custom_strategy_id: string | null;
  watchlist: string[];
  /** #203: effective Ollama request timeout (the agent's override, else the server default). */
  llm_timeout_seconds: number;
  /** #203: effective model keep-alive in minutes (-1 = keep loaded, 0 = unload after each call). */
  llm_keep_alive_minutes: number;
  /** Server defaults (OLLAMA_TIMEOUT_SECONDS / OLLAMA_KEEP_ALIVE_MINUTES), shown as hints. */
  llm_timeout_default_seconds: number;
  llm_keep_alive_default_minutes: number;
}

/** Only the editable fields; send just the ones that changed (the server rejects `{}`). */
export interface AgentConfigUpdate {
  watchlist?: string[];
  approval_mode?: string;
  llm_timeout_seconds?: number;
  llm_keep_alive_minutes?: number;
}

/** `GET /agent/strategies` entry (#189): a registered prompt, or the DYNAMIC pseudo-strategy. */
export interface Strategy {
  version: string;
  revision: string;
  name: string;
  summary: string;
  /** "FIXED" for a registered prompt, "DYNAMIC" for the auto-selecting pseudo-strategy. */
  selectable_mode: string;
  custom_strategy_id?: string | null;
}

export interface CustomStrategy {
  id: string;
  name: string;
  instructions: string;
  version: number;
  is_active: boolean;
}

export function getAgentConfig(): Promise<AgentConfig> {
  return api.get<AgentConfig>("/agent/config");
}

export function updateAgentConfig(update: AgentConfigUpdate): Promise<AgentConfig> {
  return api.patch<AgentConfig>("/agent/config", update);
}

export function listStrategies(): Promise<Strategy[]> {
  return api.get<Strategy[]>("/agent/strategies");
}

export function createCustomStrategy(
  name: string,
  instructions: string,
): Promise<CustomStrategy> {
  return api.post<CustomStrategy>("/agent/custom-strategies", { name, instructions });
}

export function updateAgentStrategy(
  agentId: string,
  strategyVersion: string,
  customStrategyId?: string,
): Promise<AgentConfig> {
  return api.put<AgentConfig>(`/agent/${agentId}/strategy`, {
    strategy_version: strategyVersion,
    ...(customStrategyId ? { custom_strategy_id: customStrategyId } : {}),
  });
}
