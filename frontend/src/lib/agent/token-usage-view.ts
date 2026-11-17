/**
 * Pure view helpers for the audit timeline's per-cycle token-usage section (#195).
 *
 * Kept free of React so they can be unit-tested in the node test environment.
 */
import type { CycleTokenUsage } from "./cycles-api";

/**
 * Percentage of the model's context window the cycle's input used, clamped to [0, 100]
 * so it can drive a progress bar's width even when `context_over_limit` pushed the raw
 * ratio past 100%.
 */
export function tokenUsagePercent(usage: CycleTokenUsage): number {
  if (usage.model_window_tokens <= 0) return 0;
  const pct = (usage.estimated_input_tokens / usage.model_window_tokens) * 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}
