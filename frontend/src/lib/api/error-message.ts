import { ApiError } from "./errors";

/** Extracts a human-readable message from an API error (FastAPI's `detail` shape). */
export function getErrorMessage(
  error: unknown,
  fallback = "Something went wrong. Please try again.",
): string {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (typeof first?.msg === "string") return first.msg;
    }
    if (error.status === 401) return "Invalid email or password.";
    if (error.status === 409) return "That email is already registered.";
  }
  if (error instanceof Error) return error.message;
  return fallback;
}
