import { afterEach, describe, expect, it, vi } from "vitest";
import { login, logout, register } from "./auth-api";
import { getToken, setToken } from "./token-store";

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(response: Response) {
  const spy = vi.fn().mockResolvedValue(response);
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
}

afterEach(() => {
  logout();
  vi.restoreAllMocks();
});

describe("auth-api", () => {
  it("login posts credentials without auth and persists the returned token", async () => {
    const spy = mockFetch(jsonResponse(200, { access_token: "jwt-xyz", token_type: "bearer" }));

    const res = await login({ email: "operator@example.com", password: "pw" });

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/auth/login");
    expect(init.method).toBe("POST");
    // Login is a public request: no Authorization header attached.
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
    expect(res.access_token).toBe("jwt-xyz");
    expect(getToken()).toBe("jwt-xyz");
  });

  it("register posts to the register endpoint without auth", async () => {
    const spy = mockFetch(jsonResponse(201, {}));

    await register({ email: "new-op@example.com", password: "pw" });

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/auth/register");
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("logout clears the stored token", () => {
    setToken("jwt-active");
    expect(getToken()).toBe("jwt-active");

    logout();

    expect(getToken()).toBeNull();
  });
});
