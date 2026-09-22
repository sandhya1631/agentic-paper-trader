/**
 * JWT token store.
 *
 * Holds the current auth token used by the API client. Backed by an in-memory
 * cache and persisted to `localStorage` when available, so a page refresh keeps
 * the session. All `window`/`localStorage` access is guarded to stay safe
 * during server-side rendering and in environments where storage is blocked.
 *
 * The full login flow that populates this store arrives in Story 0.2.2 (#18);
 * this module gives the API client a single, testable place to read/write the
 * token in the meantime.
 */
const STORAGE_KEY = "apt.auth.token";

let inMemoryToken: string | null = null;

function safeLocalStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    // Access to storage can throw (e.g. privacy mode) — degrade gracefully.
    return null;
  }
}

/** Return the current token, or `null` if none is set. */
export function getToken(): string | null {
  if (inMemoryToken !== null) return inMemoryToken;

  const storage = safeLocalStorage();
  if (storage) {
    try {
      inMemoryToken = storage.getItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
  return inMemoryToken;
}

/** Set the current token and persist it when storage is available. */
export function setToken(token: string): void {
  inMemoryToken = token;
  try {
    safeLocalStorage()?.setItem(STORAGE_KEY, token);
  } catch {
    /* ignore */
  }
}

/** Clear the current token from memory and storage. */
export function clearToken(): void {
  inMemoryToken = null;
  try {
    safeLocalStorage()?.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
