/**
 * Auth API seam.
 *
 * Thin wrappers over the API client for the JWT auth flow, giving the login UI
 * (Story 0.2.2, #18) a single place to call. Login/registration are public
 * requests (`skipAuth`), and a successful login persists the returned JWT via
 * the token store so subsequent calls are authenticated automatically.
 *
 * NOTE: endpoint paths and payload shapes below are provisional and expected to
 * be finalized together with the backend auth work in #18.
 */
import { api } from "@/lib/api/client";
import { clearToken, setToken } from "@/lib/auth/token-store";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  password: string;
}

/** Standard JWT token response (FastAPI OAuth2 shape). */
export interface TokenResponse {
  access_token: string;
  token_type?: string;
}

/** Log in and persist the returned JWT. Throws `ApiError` on bad credentials. */
export async function login(credentials: LoginRequest): Promise<TokenResponse> {
  const res = await api.post<TokenResponse>("/auth/login", credentials, {
    skipAuth: true,
  });
  setToken(res.access_token);
  return res;
}

/** Register a new operator account. Does not log the user in. */
export async function register(details: RegisterRequest): Promise<void> {
  await api.post<void>("/auth/register", details, { skipAuth: true });
}

/** Log out by clearing the stored token. */
export function logout(): void {
  clearToken();
}
