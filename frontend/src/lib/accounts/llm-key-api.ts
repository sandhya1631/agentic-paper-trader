/**
 * The user's own OpenAI / Claude API key (#191). Like the Alpaca keys, it is write-only:
 * POSTed once to be verified and encrypted at rest, and never returned by any endpoint.
 */
import { api } from "@/lib/api/client";

/** A provider a user can connect a key for: one of GET /meta/constants `llm_key_providers`
 * (the backend validates it), so the frontend never hard-codes the list. */
export type LlmKeyProvider = string;

export interface LlmKey {
  provider: LlmKeyProvider;
  is_connected: boolean;
  last_verified_at: string | null;
}

/** Key status plus what the user's cycles run on now. The `effective_*` fields are null
 * when there is no user key and the server's key is turned off (cycles then fail). */
export interface LlmKeyStatus {
  connected: boolean;
  provider: LlmKeyProvider | null;
  last_verified_at: string | null;
  effective_source: "USER" | "SERVER" | null;
  effective_provider: string | null;
  effective_model: string | null;
}

export interface ConnectLlmKeyInput {
  provider: LlmKeyProvider;
  api_key: string;
}

/** Always resolves (200), connected or not. */
export function getLlmKeyStatus(): Promise<LlmKeyStatus> {
  return api.get<LlmKeyStatus>("/accounts/llm");
}

/** Rejects with 422 (sanitized detail) when the provider doesn't accept the key. */
export function connectLlmKey(input: ConnectLlmKeyInput): Promise<LlmKey> {
  return api.post<LlmKey>("/accounts/llm", input);
}

export function disconnectLlmKey(): Promise<void> {
  return api.delete<void>("/accounts/llm");
}
