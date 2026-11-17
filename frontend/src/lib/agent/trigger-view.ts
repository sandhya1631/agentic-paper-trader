import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";

export interface TriggerFeedback {
  tone: "neutral" | "negative";
  text: string;
}

/**
 * What Trigger Now shows when POST /agent/cycles/run fails. Pure (no React) so it is
 * unit-tested directly.
 *
 * - 409: the agent already has a cycle in progress (or parked for approval).
 * - 503 (#160): the LLM is misconfigured or unreachable. The cycle was recorded as
 *   FAILED / LLM_UNAVAILABLE and nothing was traded; the backend's detail names the
 *   cause without secrets (e.g. "LLM unavailable: Ollama API error: ReadTimeout").
 * - anything else: the backend's detail, or a generic fallback.
 */
export function describeTriggerError(error: unknown): TriggerFeedback {
  if (error instanceof ApiError && error.status === 409) {
    return { tone: "neutral", text: "A cycle is already running — try again shortly." };
  }
  if (error instanceof ApiError && error.status === 503) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    const cause = typeof detail === "string" && detail ? detail : "The LLM is unavailable";
    return {
      tone: "negative",
      text: `${cause}. The cycle was recorded as failed and nothing was traded.`,
    };
  }
  return { tone: "negative", text: getErrorMessage(error, "Could not trigger a cycle.") };
}
