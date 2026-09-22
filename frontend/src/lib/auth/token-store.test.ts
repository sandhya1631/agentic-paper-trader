import { afterEach, describe, expect, it, vi } from "vitest";
import { clearToken, getToken, setToken } from "./token-store";

const STORAGE_KEY = "apt.auth.token";

/** Minimal in-memory Storage stand-in for exercising the persistence path. */
function makeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    get length() {
      return map.size;
    },
  } as Storage;
}

afterEach(() => {
  clearToken();
  delete (globalThis as { window?: unknown }).window;
  vi.resetModules();
});

describe("token store — in-memory", () => {
  it("stores, returns, and clears a token", () => {
    expect(getToken()).toBeNull();
    setToken("jwt-abc");
    expect(getToken()).toBe("jwt-abc");
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("token store — localStorage persistence", () => {
  it("writes through to storage and removes on clear", () => {
    const storage = makeStorage();
    (globalThis as { window?: unknown }).window = { localStorage: storage };

    setToken("jwt-persist");
    expect(storage.getItem(STORAGE_KEY)).toBe("jwt-persist");

    clearToken();
    expect(storage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("loads a persisted token from storage when the in-memory cache is cold", async () => {
    const storage = makeStorage();
    storage.setItem(STORAGE_KEY, "jwt-from-storage");
    (globalThis as { window?: unknown }).window = { localStorage: storage };

    // Fresh module instance => empty in-memory cache, forcing a storage read.
    vi.resetModules();
    const freshStore = await import("./token-store");
    expect(freshStore.getToken()).toBe("jwt-from-storage");
  });
});
