/** Public entry point for the API client. */
export { api, apiFetch, setUnauthorizedHandler } from "./client";
export type { ApiRequestOptions, UnauthorizedHandler } from "./client";
export { ApiError } from "./errors";
export { getApiBaseUrl } from "./config";
export { getToken, setToken, clearToken } from "@/lib/auth/token-store";
export { login, register, logout } from "@/lib/auth/auth-api";
export type { LoginRequest, RegisterRequest, TokenResponse } from "@/lib/auth/auth-api";
