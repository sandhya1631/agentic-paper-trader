import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { getMetaConstants } from "./meta-api";

const mockedApi = vi.mocked(api);

beforeEach(() => mockedApi.get.mockReset());

describe("getMetaConstants", () => {
  it("GETs /meta/constants", async () => {
    const constants = { approval_mode: ["OBSERVE_ONLY", "REQUIRED", "AUTO"], max_watchlist_size: 20 };
    mockedApi.get.mockResolvedValue(constants);

    const result = await getMetaConstants();

    expect(mockedApi.get).toHaveBeenCalledWith("/meta/constants");
    expect(result).toBe(constants);
  });
});
