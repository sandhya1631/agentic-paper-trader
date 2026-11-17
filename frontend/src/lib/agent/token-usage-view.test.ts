import { describe, expect, it } from "vitest";
import type { CycleTokenUsage } from "./cycles-api";
import { tokenUsagePercent } from "./token-usage-view";

function usage(overrides: Partial<CycleTokenUsage> = {}): CycleTokenUsage {
  return {
    cycle_id: "c1",
    estimated_input_tokens: 900,
    model_window_tokens: 4096,
    remaining_context_tokens: 3196,
    context_over_limit: false,
    context_truncated: false,
    context_chars: 3200,
    system_prompt_chars: 800,
    symbols: 1,
    bars_used: 100,
    account_data_status: "LIVE",
    ...overrides,
  };
}

describe("tokenUsagePercent", () => {
  it("rounds the input/window ratio to the nearest percent", () => {
    expect(tokenUsagePercent(usage({ estimated_input_tokens: 900, model_window_tokens: 4096 }))).toBe(22);
  });

  it("clamps at 100 when over the limit", () => {
    expect(tokenUsagePercent(usage({ estimated_input_tokens: 5000, model_window_tokens: 4096 }))).toBe(100);
  });

  it("returns 0 for a zero or missing model window instead of dividing by zero", () => {
    expect(tokenUsagePercent(usage({ model_window_tokens: 0 }))).toBe(0);
  });
});
