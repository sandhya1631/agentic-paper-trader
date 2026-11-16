import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { getCycle, listCycles } from "./cycles-api";

const mockedApi = vi.mocked(api);

beforeEach(() => mockedApi.get.mockReset());

describe("listCycles", () => {
  it("fetches with no query string when no filters are given", async () => {
    mockedApi.get.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });

    await listCycles();

    expect(mockedApi.get).toHaveBeenCalledWith("/agent/cycles");
  });

  it("builds a query string from pagination and filters (#82)", async () => {
    mockedApi.get.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0, next_offset: null });

    await listCycles({
      limit: 10,
      offset: 20,
      startedAfter: "2026-11-01T00:00:00Z",
      startedBefore: "2026-11-05T00:00:00Z",
      action: "BUY",
      status: "FAILED",
      policyDecision: "DENY",
    });

    const [url] = mockedApi.get.mock.calls[0] as [string];
    const query = new URLSearchParams(url.split("?")[1]);
    expect(url.startsWith("/agent/cycles?")).toBe(true);
    expect(query.get("limit")).toBe("10");
    expect(query.get("offset")).toBe("20");
    expect(query.get("started_after")).toBe("2026-11-01T00:00:00Z");
    expect(query.get("started_before")).toBe("2026-11-05T00:00:00Z");
    expect(query.get("action")).toBe("BUY");
    expect(query.get("status")).toBe("FAILED");
    expect(query.get("policy_decision")).toBe("DENY");
  });

  it("omits filters that are not set", async () => {
    mockedApi.get.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });

    await listCycles({ action: "SELL" });

    expect(mockedApi.get).toHaveBeenCalledWith("/agent/cycles?action=SELL");
  });
});

describe("getCycle", () => {
  it("fetches a single cycle by id", async () => {
    const cycle = { id: "c1" };
    mockedApi.get.mockResolvedValue(cycle);

    const result = await getCycle("c1");

    expect(mockedApi.get).toHaveBeenCalledWith("/agent/cycles/c1");
    expect(result).toBe(cycle);
  });
});
