import { describe, expect, it } from "vitest";
import { describeLlmHealth, describeReason, describeTick } from "./scheduler-log-view";

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
