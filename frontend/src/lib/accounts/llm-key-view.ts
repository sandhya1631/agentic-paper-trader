import type { LlmKeyStatus } from "./llm-key-api";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Claude",
  ollama: "Ollama",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
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
