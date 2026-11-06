/**
 * Auth API seam.
 *
 * Thin wrappers over the API client for the JWT auth flow, used by the login/
 * register UI (#39). Login/registration are public requests (`skipAuth`), and
 * a successful login persists the returned JWT via the token store so
 * subsequent calls are authenticated automatically.
 *
 * Payload shape matches the backend's auth contract (Story 0.2.2, #18):
 * `UserLogin`/`UserCreate` take `email` + `password`, not `username`.
 */
import { api } from "@/lib/api/client";
import { clearToken, setToken } from "@/lib/auth/token-store";

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
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

export interface ForgotPasswordResponse {
  detail: string;
}

export interface ResetPasswordRequest {
  token: string;
  new_password: string;
}

export interface ResetPasswordResponse {
  detail: string;
}

/**
 * Request a password reset link (#166). Always resolves with the same generic
 * message, whether or not the email is registered — the backend never reveals
 * that, so the UI must not try to infer it either.
 */
export async function forgotPassword(email: string): Promise<ForgotPasswordResponse> {
  return api.post<ForgotPasswordResponse>(
    "/auth/forgot-password",
    { email },
    { skipAuth: true },
  );
}

/** Consume a one-time reset token and set a new password. Does not log the user in. */
export async function resetPassword(
  payload: ResetPasswordRequest,
): Promise<ResetPasswordResponse> {
  return api.post<ResetPasswordResponse>("/auth/reset-password", payload, {
    skipAuth: true,
  });
}
