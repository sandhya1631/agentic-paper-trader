import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import {
  describeLlmHealth,
  describeReason,
  describeTick,
  llmHealthFromProbeError,
} from "./scheduler-log-view";

describe("describeReason", () => {
  it("maps known codes and humanizes unknown ones", () => {
    expect(describeReason(null)).toBeNull();
    expect(describeReason("market_closed")).toBe("Market closed");
    expect(describeReason("something_else")).toBe("something else");
  });
});

describe("describeTick", () => {
  it("labels a completed tick as Ran (positive)", () => {
    expect(describeTick({ status: "COMPLETED" })).toMatchObject({ label: "Ran", tone: "positive" });
  });

  it("labels a skipped market-closed tick (neutral) with a reason", () => {
    expect(describeTick({ status: "SKIPPED", reason: "market_closed" })).toMatchObject({
      label: "Skipped",
      tone: "neutral",
      reason: "Market closed",
    });
  });

  it("labels a tick skipped for an owner who isn't an operator (#176)", () => {
    expect(describeTick({ status: "SKIPPED", reason: "owner_not_operator" })).toMatchObject({
      label: "Skipped",
      tone: "neutral",
      reason: "Owner isn't an operator or admin",
    });
  });

  it("labels a failed tick (negative)", () => {
    expect(describeTick({ status: "FAILED", reason: "agent_cycle_failed" })).toMatchObject({
      label: "Failed",
      tone: "negative",
      reason: "Agent cycle failed",
    });
  });

  it("labels a partial tick (negative)", () => {
    expect(describeTick({ status: "PARTIAL" })).toMatchObject({ label: "Partial", tone: "negative" });
  });

  it("falls back for an unknown status", () => {
    expect(describeTick({})).toMatchObject({ label: "Unknown", tone: "neutral" });
  });
});

describe("describeLlmHealth", () => {
  it("is reachable when the fetch succeeded", () => {
    const v = describeLlmHealth({ provider: "ollama", model: "llama3.1" }, true);
    expect(v).toMatchObject({ reachable: true, label: "Reachable", detail: "ollama · llama3.1" });
  });

  it("is unreachable when the fetch failed", () => {
    expect(describeLlmHealth(null, false)).toMatchObject({ reachable: false, label: "Unreachable" });
  });

  it("honors an explicit reachable=false flag even on a 200", () => {
    expect(describeLlmHealth({ provider: "ollama", reachable: false }, true).reachable).toBe(false);
  });

  it("flags a missing model", () => {
    const v = describeLlmHealth({ provider: "ollama", model: "x", model_available: false }, true);
    expect(v).toMatchObject({ reachable: true, label: "Model missing" });
  });
});

describe("llmHealthFromProbeError", () => {
  const down = {
    provider: "anthropic",
    model: "claude-opus-5-5",
    reachable: false,
    model_available: false,
    detail: "LLM client unavailable: ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic",
  };

  it("turns the probe's 503 payload into an unreachable view with the backend's reason", () => {
    const health = llmHealthFromProbeError(new ApiError(503, "Service Unavailable", down, "/llm/health"));
    expect(health).toEqual(down);
    expect(describeLlmHealth(health, true)).toMatchObject({
      reachable: false,
      label: "Unreachable",
      detail: down.detail,
    });
  });

  it("ignores anything that is not a 503 health payload", () => {
    expect(llmHealthFromProbeError(new ApiError(500, "Server Error", down, "/llm/health"))).toBeNull();
    expect(llmHealthFromProbeError(new ApiError(503, "Unavailable", "busy", "/llm/health"))).toBeNull();
    expect(
      llmHealthFromProbeError(new ApiError(503, "Unavailable", { reachable: true }, "/llm/health")),
    ).toBeNull();
    expect(llmHealthFromProbeError(new TypeError("Failed to fetch"))).toBeNull();
  });
});
