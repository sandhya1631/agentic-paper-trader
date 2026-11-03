/**
 * Pure presentation helpers for scheduler tick logs and LLM health (#143). Kept
 * React-free so they unit-test in the node environment.
 */
import type { LlmHealth, SchedulerTick } from "./observability-api";

export type TickTone = "positive" | "negative" | "neutral";

export interface TickView {
  label: string;
  tone: TickTone;
  reason: string | null;
}

/** Human-readable reason text for the backend's snake_case reason codes. */
const REASON_TEXT: Record<string, string> = {
  kill_switch_enabled: "Kill switch enabled",
  kill_switch_check_failed: "Kill-switch check failed",
  no_enabled_agents: "No enabled agents",
  agent_query_failed: "Agent lookup failed",
  market_clock_check_failed: "Market-clock check failed",
  market_closed: "Market closed",
  some_agent_cycles_failed: "Some agent cycles failed",
  agent_cycle_failed: "Agent cycle failed",
  all_agents_already_running: "All agents already running",
  some_agents_already_running: "Some agents already running",
};

export function describeReason(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_TEXT[reason] ?? reason.replace(/_/g, " ");
}

/** Map a tick's status to a label + badge tone for the activity panel. */
export function describeTick(tick: SchedulerTick): TickView {
  const status = (tick.status ?? "").toUpperCase();
  const reason = describeReason(tick.reason);
  switch (status) {
    case "COMPLETED":
      return { label: "Ran", tone: "positive", reason };
    case "SKIPPED":
      return { label: "Skipped", tone: "neutral", reason };
    case "PARTIAL":
      return { label: "Partial", tone: "negative", reason };
    case "FAILED":
      return { label: "Failed", tone: "negative", reason };
    default:
      return { label: status || "Unknown", tone: "neutral", reason };
  }
}

export interface LlmHealthView {
  reachable: boolean;
  label: string;
  detail: string;
}

/**
 * Derive a reachable/unreachable view. `fetchOk` is whether the request itself
 * succeeded; an explicit `reachable` flag in the payload overrides it.
 */
export function describeLlmHealth(
  health: LlmHealth | null,
  fetchOk: boolean,
): LlmHealthView {
  const reachable = health?.reachable ?? fetchOk;
  const provider = health?.provider ?? "LLM";
  const model = health?.model;
  const base = model ? `${provider} · ${model}` : provider;
  if (!reachable) {
    return { reachable: false, label: "Unreachable", detail: health?.detail ?? base };
  }
  const modelAvailable = health?.model_available;
  if (modelAvailable === false) {
    return { reachable: true, label: "Model missing", detail: base };
  }
  return { reachable: true, label: "Reachable", detail: base };
}
