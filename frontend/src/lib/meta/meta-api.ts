/**
 * Backend constants/enums (#139): read these instead of hardcoding enum strings
 * or limits (e.g. the approval modes and the watchlist size cap) that would
 * silently go stale if the backend ever renames or adds a value.
 */
import { api } from "@/lib/api/client";

export interface Constants {
  currency: string;
  cycle_trigger: string[];
  cycle_status: string[];
  decision_action: string[];
  policy_decision_type: string[];
  approval_mode: string[];
  approval_status: string[];
  /** Most symbols an agent's watchlist may hold (the backend rejects more with 422). */
  max_watchlist_size: number;
  /** The watchlist a new agent starts with (the Market page's fallback with no agent yet). */
  default_watchlist: string[];
  /** Inclusive [min, max] for an agent's Ollama request timeout, in seconds (#203). */
  llm_timeout_seconds_range?: number[];
  /** Inclusive [min, max] for an agent's model keep-alive, in minutes (-1 = keep loaded). */
  llm_keep_alive_minutes_range?: number[];
  /** Providers a user may connect their own API key for, in display order (#191). */
  llm_key_providers?: string[];
}

export function getConstants(): Promise<Constants> {
  return api.get<Constants>("/meta/constants");
}
