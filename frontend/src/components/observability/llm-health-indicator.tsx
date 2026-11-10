"use client";

import { useCallback, useEffect, useState } from "react";
import { getLlmHealth, type LlmHealth } from "@/lib/agent/observability-api";
import { describeLlmHealth } from "@/lib/agent/scheduler-log-view";
import { usePolling } from "@/lib/hooks/use-polling";

// Each poll probes the provider (Ollama's tags, Anthropic's/OpenAI's models
// endpoint), so poll less often than the cheap data views.
const REFRESH_SECONDS = 60;

interface HealthState {
  loaded: boolean;
  health: LlmHealth | null;
  ok: boolean;
}

/** Compact LLM health pill for the header (#143). Polls `/llm/health?probe=true`. */
export function LlmHealthIndicator() {
  const [state, setState] = useState<HealthState>({ loaded: false, health: null, ok: false });

  const load = useCallback(async () => {
    const [res] = await Promise.allSettled([getLlmHealth()]);
    if (res.status === "fulfilled") {
      setState({ loaded: true, health: res.value, ok: true });
    } else {
      setState({ loaded: true, health: null, ok: false });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(() => void load(), REFRESH_SECONDS * 1000);

  if (!state.loaded) {
    return (
      <span className="hidden items-center gap-2 rounded-full border border-border px-3 py-1 text-xs font-medium text-muted sm:inline-flex">
        LLM …
      </span>
    );
  }

  const view = describeLlmHealth(state.health, state.ok);
  return (
    <span
      className="hidden items-center gap-2 rounded-full border border-border bg-surface-muted px-3 py-1 text-xs font-medium text-muted sm:inline-flex"
      title={view.detail}
    >
      <span
        className={`h-2 w-2 rounded-full ${view.reachable ? "bg-positive" : "bg-negative"}`}
        aria-hidden
      />
      LLM: {view.label}
    </span>
  );
}
