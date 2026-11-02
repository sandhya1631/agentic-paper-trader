// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/agent/observability-api", () => ({ getLlmHealth: vi.fn() }));

import { getLlmHealth } from "@/lib/agent/observability-api";
import { LlmHealthIndicator } from "./llm-health-indicator";

const mockHealth = vi.mocked(getLlmHealth);

beforeEach(() => mockHealth.mockReset());
afterEach(() => cleanup());

// Note: the "fetch failed -> unreachable" mapping is covered by the pure
// describeLlmHealth unit test (scheduler-log-view.test.ts). Here we drive the
// component's two rendered states from the response payload.
describe("LlmHealthIndicator", () => {
  it("shows reachable with provider/model when the endpoint responds", async () => {
    mockHealth.mockResolvedValue({ provider: "ollama", model: "llama3.1" });
    render(<LlmHealthIndicator />);
    expect(await screen.findByText(/LLM: Reachable/)).toBeTruthy();
  });

  it("shows unreachable when the payload reports it", async () => {
    mockHealth.mockResolvedValue({ provider: "ollama", reachable: false });
    render(<LlmHealthIndicator />);
    expect(await screen.findByText(/LLM: Unreachable/)).toBeTruthy();
  });
});
