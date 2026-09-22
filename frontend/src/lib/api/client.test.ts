import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiFetch, setUnauthorizedHandler } from "./client";
import { ApiError } from "./errors";
import { clearToken, getToken, setToken } from "@/lib/auth/token-store";

function jsonResponse(status: number, data: unknown): Response {
  const body = status === 204 ? null : JSON.stringify(data);
  return new Response(body, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(response: Response) {
  const spy = vi.fn().mockResolvedValue(response);
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
}

/** Read the RequestInit passed to the mocked fetch on its first call. */
function firstCallInit(spy: ReturnType<typeof vi.fn>): RequestInit {
  return spy.mock.calls[0][1] as RequestInit;
}

afterEach(() => {
  clearToken();
  setUnauthorizedHandler(null);
  vi.restoreAllMocks();
});

describe("request interceptor — Bearer token injection", () => {
  it("attaches Authorization: Bearer <token> when a token is set", async () => {
    setToken("test-jwt-123");
    const spy = mockFetch(jsonResponse(200, { ok: true }));

    await apiFetch("/portfolio");

    const headers = new Headers(firstCallInit(spy).headers);
    expect(headers.get("Authorization")).toBe("Bearer test-jwt-123");
  });

  it("omits Authorization when no token is set", async () => {
    const spy = mockFetch(jsonResponse(200, {}));

    await apiFetch("/public");

    const headers = new Headers(firstCallInit(spy).headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("respects skipAuth for public endpoints (e.g. login)", async () => {
    setToken("should-not-be-sent");
    const spy = mockFetch(jsonResponse(200, {}));

    await apiFetch("/auth/login", { skipAuth: true });

    const headers = new Headers(firstCallInit(spy).headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("serializes object bodies as JSON via api.post", async () => {
    const spy = mockFetch(jsonResponse(201, { id: 1 }));

    await api.post("/orders", { symbol: "AAPL", qty: 1 });

    const init = firstCallInit(spy);
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ symbol: "AAPL", qty: 1 }));
  });
});

describe("response interceptor — global 401 handling", () => {
  it("clears the token and invokes the registered handler on 401", async () => {
    setToken("expired-jwt");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch(jsonResponse(401, { detail: "Unauthorized" }));

    await expect(apiFetch("/portfolio")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0]).toMatchObject({ url: expect.stringContaining("/portfolio") });
    expect(getToken()).toBeNull();
  });

  it("throws an ApiError carrying status and parsed body on non-2xx", async () => {
    mockFetch(jsonResponse(422, { detail: "Invalid symbol" }));

    await expect(apiFetch("/orders")).rejects.toMatchObject({
      status: 422,
      body: { detail: "Invalid symbol" },
    });
  });

  it("does not fire the unauthorized handler for successful requests", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch(jsonResponse(200, { ok: true }));

    await apiFetch("/portfolio");

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not treat a skipAuth 401 (failed login) as an expired session", async () => {
    setToken("existing-session");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch(jsonResponse(401, { detail: "Bad credentials" }));

    await expect(apiFetch("/auth/login", { skipAuth: true })).rejects.toBeInstanceOf(ApiError);
    // The unauthorized handler must NOT fire and the existing token is preserved.
    expect(handler).not.toHaveBeenCalled();
    expect(getToken()).toBe("existing-session");
  });

  it("suppresses global 401 handling when suppressUnauthorized is set", async () => {
    setToken("keep-me");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch(jsonResponse(401, {}));

    await expect(
      apiFetch("/portfolio", { suppressUnauthorized: true }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
    expect(getToken()).toBe("keep-me");
  });
});
