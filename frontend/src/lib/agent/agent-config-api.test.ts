import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn(), patch: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { getAgentConfig, updateAgentConfig } from "./agent-config-api";

const mockedApi = vi.mocked(api);

beforeEach(() => {
  mockedApi.get.mockReset();
  mockedApi.patch.mockReset();
});

describe("getAgentConfig", () => {
  it("GETs /agent/config", async () => {
    const config = { id: "a1", watchlist: ["AAPL"], approval_mode: "REQUIRED" };
    mockedApi.get.mockResolvedValue(config);

    const result = await getAgentConfig();

    expect(mockedApi.get).toHaveBeenCalledWith("/agent/config");
    expect(result).toBe(config);
  });
});

describe("updateAgentConfig", () => {
  it("PATCHes /agent/config with only the given fields", async () => {
    mockedApi.patch.mockResolvedValue({ id: "a1" });

    await updateAgentConfig({ watchlist: ["AAPL", "MSFT"] });

    expect(mockedApi.patch).toHaveBeenCalledWith("/agent/config", {
      watchlist: ["AAPL", "MSFT"],
    });
  });

  it("passes backend errors through to the caller", async () => {
    mockedApi.patch.mockRejectedValue(new Error("boom"));

    await expect(updateAgentConfig({ approval_mode: "AUTO" })).rejects.toThrow("boom");
  });
});
