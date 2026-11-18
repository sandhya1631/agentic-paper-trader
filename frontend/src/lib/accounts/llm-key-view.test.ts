import { describe, expect, it } from "vitest";

import type { LlmKeyStatus } from "./llm-key-api";
import { describeLlmKeySource, providerLabel } from "./llm-key-view";

function status(overrides: Partial<LlmKeyStatus>): LlmKeyStatus {
  return {
    connected: false,
    provider: null,
    last_verified_at: null,
    effective_source: "SERVER",
    effective_provider: "openai",
    effective_model: "gpt-4o-mini",
    ...overrides,
  };
}

describe("describeLlmKeySource", () => {
  it("names the user's own key", () => {
    expect(
      describeLlmKeySource(
        status({
          connected: true,
          provider: "anthropic",
          effective_source: "USER",
          effective_provider: "anthropic",
          effective_model: "claude-opus-5-5",
        }),
      ),
    ).toBe("Cycles use: your Claude key (claude-opus-5-5)");
  });

  it("names the server's key when the user has none", () => {
    expect(describeLlmKeySource(status({}))).toBe(
      "Cycles use: the server's key (OpenAI, gpt-4o-mini)",
    );
  });

  it("says cycles can't run when there's no key and no server fallback", () => {
    expect(
      describeLlmKeySource(
        status({ effective_source: null, effective_provider: null, effective_model: null }),
      ),
    ).toBe("Cycles can't run: connect a key (the server's key is turned off).");
  });
});

describe("providerLabel", () => {
  it("labels known providers and passes others through", () => {
    expect(providerLabel("openai")).toBe("OpenAI");
    expect(providerLabel("anthropic")).toBe("Claude");
    expect(providerLabel("ollama")).toBe("Ollama");
    expect(providerLabel("other")).toBe("other");
  });
});
