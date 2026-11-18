import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";

/** Prefix of the backend's 503 detail for an unavailable LLM (#160). */
const LLM_UNAVAILABLE_PREFIX = "LLM unavailable:";

export interface TriggerFeedback {
  tone: "neutral" | "negative";
  text: string;
}

/**
 * What Trigger Now shows when POST /agent/cycles/run fails. Pure (no React) so it is
 * unit-tested directly.
 *
 * - 409: the agent already has a cycle in progress (or parked for approval).
 * - 503 with "LLM unavailable: ..." (#160): the LLM is misconfigured or unreachable.
 *   The cycle was recorded as FAILED / LLM_UNAVAILABLE and nothing was traded; the
 *   detail names the cause without secrets (e.g. "LLM unavailable: Ollama API error:
 *   ReadTimeout"). Any other 503 (e.g. Render waking up) created no cycle.
 * - anything else: the backend's detail, or a generic fallback.
 */
export function describeTriggerError(error: unknown): TriggerFeedback {
  if (error instanceof ApiError && error.status === 409) {
    return { tone: "neutral", text: "A cycle is already running — try again shortly." };
  }
  const text = getErrorMessage(error, "Could not trigger a cycle.");
  // Only the backend's own LLM 503 means a FAILED cycle was recorded; a 503 from Render's
  // proxy while the API wakes up (no JSON detail) created no cycle at all.
  const llmDown = text.startsWith(LLM_UNAVAILABLE_PREFIX);
  if (error instanceof ApiError && error.status === 503 && llmDown) {
    return {
      tone: "negative",
      text: `${text}. The cycle was recorded as failed and nothing was traded.`,
    };
  }
  if (error instanceof ApiError && error.status === 503) {
    return {
      tone: "negative",
      text: "The server is unavailable (it may be starting up). Try again in a minute.",
    };
  }
  return { tone: "negative", text };
}
