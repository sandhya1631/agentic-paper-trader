/**
 * Centralized API client (fetch wrapper) with auth interceptors.
 *
 * Story 0.1.2 (#16):
 *  - Request interceptor: attaches `Authorization: Bearer <token>` automatically.
 *  - Response interceptor: handles 401 Unauthorized globally (drops the token
 *    and notifies a registered handler / emits a browser event).
 *
 * Use the `api` helpers (`api.get`, `api.post`, ...) for typed JSON calls, or
 * `apiFetch` directly for full control.
 */
import { getApiBaseUrl } from "./config";
import { clearToken, getToken } from "@/lib/auth/token-store";
import { ApiError } from "./errors";

export type UnauthorizedHandler = (context: {
  response: Response;
  url: string;
}) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/**
 * Register a global handler invoked whenever the API returns 401. The app wires
 * this to a redirect once the login route exists (#18). Pass `null` to reset.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function handleUnauthorized(response: Response, url: string): void {
  // The stored token is no longer valid — drop it so subsequent requests are
  // unauthenticated rather than replaying a dead token.
  clearToken();

  if (unauthorizedHandler) {
    unauthorizedHandler({ response, url });
    return;
  }

  // Default behavior: emit a browser event so UI code can react (e.g. redirect).
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("auth:unauthorized"));
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  /** JSON-serializable value (objects are stringified) or a raw BodyInit. */
  body?: unknown;
  /** Skip attaching the Authorization header for this request. */
  skipAuth?: boolean;
  /**
   * Skip global 401 handling for this request. Implied by `skipAuth`: a 401 from
   * a public/auth request (e.g. a failed login) is a credentials error, not an
   * expired session, so it must not clear the token or trigger the global handler.
   */
  suppressUnauthorized?: boolean;
  /** Override the API base URL for this request. */
  baseUrl?: string;
}

function buildUrl(path: string, baseUrl: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const trimmedBase = baseUrl.replace(/\/+$/, "");
  const trimmedPath = path.replace(/^\/+/, "");
  return `${trimmedBase}/${trimmedPath}`;
}

function isRawBody(body: unknown): boolean {
  return (
    typeof body === "string" ||
    body instanceof FormData ||
    body instanceof Blob ||
    body instanceof ArrayBuffer ||
    body instanceof URLSearchParams ||
    (typeof ReadableStream !== "undefined" && body instanceof ReadableStream)
  );
}

async function parseBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205) {
    return undefined as T;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  const text = await response.text();
  return (text.length > 0 ? text : undefined) as T;
}

async function safeParse(response: Response): Promise<unknown> {
  try {
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) return await response.json();
    return await response.text();
  } catch {
    return null;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { body, skipAuth, suppressUnauthorized, baseUrl, headers, ...rest } = options;

  const finalHeaders = new Headers(headers);

  // --- Request interceptor: attach the JWT as a Bearer token ---
  if (!skipAuth) {
    const token = getToken();
    if (token && !finalHeaders.has("Authorization")) {
      finalHeaders.set("Authorization", `Bearer ${token}`);
    }
  }

  // Serialize plain-object bodies as JSON; pass raw bodies through untouched.
  let finalBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (isRawBody(body)) {
      finalBody = body as BodyInit;
    } else {
      if (!finalHeaders.has("Content-Type")) {
        finalHeaders.set("Content-Type", "application/json");
      }
      finalBody = JSON.stringify(body);
    }
  }

  const url = buildUrl(path, baseUrl ?? getApiBaseUrl());
  const response = await fetch(url, { ...rest, headers: finalHeaders, body: finalBody });

  // --- Response interceptor: global 401 handling ---
  // Skipped for public/auth requests, whose 401 means "bad credentials" rather
  // than "session expired".
  if (response.status === 401 && !skipAuth && !suppressUnauthorized) {
    handleUnauthorized(response, url);
  }

  if (!response.ok) {
    const errorBody = await safeParse(response);
    throw new ApiError(response.status, response.statusText, errorBody, url);
  }

  return parseBody<T>(response);
}

type BodylessOptions = Omit<ApiRequestOptions, "body" | "method">;
type BodyOptions = Omit<ApiRequestOptions, "body" | "method">;

/** Convenience helpers for common HTTP verbs (return parsed JSON). */
export const api = {
  get: <T = unknown>(path: string, options?: BodylessOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T = unknown>(path: string, body?: unknown, options?: BodyOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", body }),
  put: <T = unknown>(path: string, body?: unknown, options?: BodyOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT", body }),
  patch: <T = unknown>(path: string, body?: unknown, options?: BodyOptions) =>
    apiFetch<T>(path, { ...options, method: "PATCH", body }),
  delete: <T = unknown>(path: string, options?: BodylessOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};
