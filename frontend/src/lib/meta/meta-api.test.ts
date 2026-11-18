import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { getConstants, type Constants } from "./meta-api";

const mockedApi = vi.mocked(api);

beforeEach(() => mockedApi.get.mockReset());

describe("getConstants", () => {
  it("fetches /meta/constants", async () => {
    const constants: Constants = {
      currency: "USD",
      cycle_trigger: ["MANUAL", "SCHEDULED"],
      cycle_status: ["COMPLETED", "FAILED"],
      decision_action: ["BUY", "SELL", "HOLD"],
      policy_decision_type: ["ALLOW", "DENY", "RESIZE", "NOT_EVALUATED"],
      approval_mode: ["AUTO", "REQUIRED", "OBSERVE_ONLY"],
      approval_status: ["PENDING", "APPROVED", "REJECTED"],
      max_watchlist_size: 20,
      default_watchlist: ["AAPL", "NVDA", "SPY"],
      llm_key_providers: ["openai", "anthropic"],
    };
    mockedApi.get.mockResolvedValue(constants);

    const result = await getConstants();

    expect(mockedApi.get).toHaveBeenCalledWith("/meta/constants");
    expect(result).toBe(constants);
    // The AI key card's provider choices (#191) come from here, not a frontend list.
    expect(result.llm_key_providers).toEqual(["openai", "anthropic"]);
  });
});
