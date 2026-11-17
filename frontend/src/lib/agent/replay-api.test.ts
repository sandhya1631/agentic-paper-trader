import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { post: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { runReplay } from "./replay-api";

const mockedApi = vi.mocked(api);

beforeEach(() => mockedApi.post.mockReset());

describe("runReplay", () => {
  it("posts the strategy version and samples to /agent/replay", async () => {
    const request = {
      strategy_version: "S-001",
      samples: [
        {
          symbols: ["AAPL"],
          bars_per_symbol: { AAPL: [] },
          next_prices: { AAPL: 190 },
        },
      ],
    };
    mockedApi.post.mockResolvedValue({
      strategy_version: "S-001",
      samples: 1,
      valid_decisions: 1,
      invalid_decisions: 0,
      action_counts: { BUY: 1, SELL: 0, HOLD: 0 },
      average_input_tokens: 500,
      average_latency_ms: 120,
      cumulative_return_pct: 1.5,
      buy_and_hold_return_pct: 1.2,
    });

    const result = await runReplay(request);

    expect(mockedApi.post).toHaveBeenCalledWith("/agent/replay", request);
    expect(result.valid_decisions).toBe(1);
  });
});
