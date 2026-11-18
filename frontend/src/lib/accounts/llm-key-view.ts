import type { LlmKeyStatus } from "./llm-key-api";

// Display names only. Which providers a user may connect comes from the backend
// (GET /meta/constants `llm_key_providers`); an unknown value shows as-is.
const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Claude",
  ollama: "Ollama",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

/**
 * The provider choices for the AI key card (#191): `llm_key_providers` from
 * GET /meta/constants, in the backend's order, without blanks or duplicates. Empty when
 * the constants couldn't be loaded; the card then can't connect a key.
 */
export function llmKeyProviderOptions(providers: readonly string[] | null | undefined): string[] {
  return [...new Set((providers ?? []).filter((provider) => provider.trim() !== ""))];
}

/** The AI provider key card's status line: whose key the user's cycles run on (#191). */
export function describeLlmKeySource(status: LlmKeyStatus): string {
  const provider = status.effective_provider ? providerLabel(status.effective_provider) : "";
  const model = status.effective_model ?? "";
  if (status.effective_source === "USER") {
    return `Cycles use: your ${provider} key (${model})`;
  }
  if (status.effective_source === "SERVER") {
    return `Cycles use: the server's key (${provider}, ${model})`;
  }
  return "Cycles can't run: connect a key (the server's key is turned off).";
}
