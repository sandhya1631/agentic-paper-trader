import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { getLlmHealth } from "./observability-api";

const mockedApi = vi.mocked(api);

beforeEach(() => mockedApi.get.mockReset());

describe("getLlmHealth", () => {
  it("probes the provider instead of the config-only echo", async () => {
    const ok = { provider: "ollama", model: "llama3.1", reachable: true, model_available: true };
    mockedApi.get.mockResolvedValue(ok);

    await expect(getLlmHealth()).resolves.toBe(ok);
    expect(mockedApi.get).toHaveBeenCalledWith("/llm/health?probe=true");
  });

  it("resolves the probe's 503 health payload and rejects other failures", async () => {
    const down = { provider: "anthropic", reachable: false, detail: "Anthropic API unreachable" };
    const serverError = new ApiError(500, "Server Error", null, "/llm/health");
    mockedApi.get
      .mockRejectedValueOnce(new ApiError(503, "Service Unavailable", down, "/llm/health"))
      .mockRejectedValueOnce(serverError);

    await expect(getLlmHealth()).resolves.toEqual(down);
    await expect(getLlmHealth()).rejects.toBe(serverError);
  });
});
