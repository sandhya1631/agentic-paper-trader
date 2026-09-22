/**
 * API configuration.
 *
 * The backend base URL is read from the public env var `NEXT_PUBLIC_API_BASE_URL`
 * so dev, staging, and prod can target different hosts without code changes.
 * Falls back to the local FastAPI default (port 8000).
 */
const DEFAULT_API_BASE_URL = "http://localhost:8000";

export function getApiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  return fromEnv && fromEnv.length > 0 ? fromEnv : DEFAULT_API_BASE_URL;
}
