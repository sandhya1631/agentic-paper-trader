import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { describeTriggerError } from "@/lib/agent/trigger-view";

function apiError(status: number, detail?: string): ApiError {
  return new ApiError(status, "Error", detail ? { detail } : null, "http://api/agent/cycles/run");
}

describe("describeTriggerError", () => {
  it("shows the 503 detail for an unavailable LLM and says nothing was traded (#160)", () => {
    const feedback = describeTriggerError(
      apiError(503, "LLM unavailable: ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"),
    );
    expect(feedback.tone).toBe("negative");
    expect(feedback.text).toBe(
      "LLM unavailable: ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic. " +
        "The cycle was recorded as failed and nothing was traded.",
    );
  });

  it("names a provider timeout instead of an empty message", () => {
    expect(describeTriggerError(apiError(503, "LLM unavailable: Ollama API error: ReadTimeout")).text)
      .toContain("Ollama API error: ReadTimeout");
  });

  it("does not claim a cycle was recorded for a 503 that isn't the LLM's (Render waking up)", () => {
    const text = describeTriggerError(apiError(503)).text;
    expect(text).toContain("server is unavailable");
    expect(text).not.toContain("recorded as failed");
    expect(describeTriggerError(apiError(503, "Service Unavailable")).text).not.toContain(
      "recorded as failed",
    );
  });

  it("keeps the busy message for 409", () => {
    expect(describeTriggerError(apiError(409, "Agent already has an active cycle"))).toEqual({
      tone: "neutral",
      text: "A cycle is already running — try again shortly.",
    });
  });

  it("passes other API details through", () => {
    expect(describeTriggerError(apiError(404, "Agent not found")).text).toBe("Agent not found");
  });

  it("falls back for non-API errors", () => {
    expect(describeTriggerError("weird").text).toBe("Could not trigger a cycle.");
  });
});
