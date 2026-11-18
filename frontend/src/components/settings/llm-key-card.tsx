"use client";

import { useCallback, useEffect, useState } from "react";
import {
  connectLlmKey,
  disconnectLlmKey,
  getLlmKeyStatus,
  type LlmKeyProvider,
  type LlmKeyStatus,
} from "@/lib/accounts/llm-key-api";
import {
  describeLlmKeySource,
  llmKeyProviderOptions,
  providerLabel,
} from "@/lib/accounts/llm-key-view";
import { getErrorMessage } from "@/lib/api/error-message";
import { getConstants } from "@/lib/meta/meta-api";
import { Badge } from "@/components/ui/badge";

/** Settings card for the user's own OpenAI / Claude key (#191), under the Alpaca card.
 * Any signed-in user may connect one: it only affects their own agent's cycles. */
export function LlmKeyCard() {
  const [status, setStatus] = useState<LlmKeyStatus | null>(null);
  const [loading, setLoading] = useState(true);
  // The choices come from GET /meta/constants (null while loading, [] if it failed).
  const [providers, setProviders] = useState<string[] | null>(null);
  const [provider, setProvider] = useState<LlmKeyProvider>("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await getLlmKeyStatus());
    } catch (e) {
      setError(getErrorMessage(e, "Could not read the AI provider key status."));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let options: string[] = [];
      try {
        options = llmKeyProviderOptions((await getConstants())?.llm_key_providers);
      } catch {
        // Leave the list empty: the form says the providers couldn't be loaded.
      }
      if (!cancelled) setProviders(options);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The chosen provider, else the backend's first one.
  const selectedProvider =
    providers && providers.includes(provider) ? provider : (providers?.[0] ?? "");
  const noProviders = providers !== null && providers.length === 0;

  async function onConnect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const key = apiKey.trim();
    // Clear the field whatever happens: the key never lingers in the page.
    setApiKey("");
    try {
      await connectLlmKey({ provider: selectedProvider, api_key: key });
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not connect the key."));
    } finally {
      setBusy(false);
    }
  }

  async function onDisconnect() {
    setBusy(true);
    setError(null);
    try {
      await disconnectLlmKey();
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not disconnect the key."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h3 className="text-sm font-semibold">AI provider key</h3>
      {error && (
        <p role="alert" className="mt-2 text-sm text-negative">
          {error}
        </p>
      )}
      {loading ? (
        <p className="mt-1 text-sm text-muted">Loading key status...</p>
      ) : (
        <>
          {status && <p className="mt-1 text-sm text-muted">{describeLlmKeySource(status)}</p>}
          {status?.connected && status.provider ? (
            <div className="mt-2 space-y-2 text-sm">
              <p>
                <Badge tone="positive">Connected</Badge>{" "}
                <span className="text-muted">{providerLabel(status.provider)}</span>
              </p>
              <button
                type="button"
                onClick={() => void onDisconnect()}
                disabled={busy}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
              >
                {busy ? "Working..." : "Disconnect"}
              </button>
            </div>
          ) : (
            <form onSubmit={onConnect} className="mt-2 space-y-3">
              <p className="text-sm text-muted">
                Connect your own OpenAI or Claude API key so your agent&apos;s cycles bill your
                account. The key is checked with the provider, encrypted at rest and never shown
                again.
              </p>
              <div>
                <label htmlFor="llm-provider" className="text-xs font-medium text-muted">
                  Provider
                </label>
                <select
                  id="llm-provider"
                  value={selectedProvider}
                  onChange={(e) => setProvider(e.target.value)}
                  disabled={!providers || noProviders}
                  className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                >
                  {(providers ?? []).map((option) => (
                    <option key={option} value={option}>
                      {providerLabel(option)}
                    </option>
                  ))}
                </select>
                {noProviders && (
                  <p className="mt-1 text-sm text-negative">
                    Could not load the AI providers. Reload the page to connect a key.
                  </p>
                )}
              </div>
              <div>
                <label htmlFor="llm-api-key" className="text-xs font-medium text-muted">
                  Provider API key
                </label>
                <input
                  id="llm-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  autoComplete="off"
                  required
                  className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={busy || !apiKey.trim() || !selectedProvider}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
              >
                {busy ? "Connecting..." : "Connect key"}
              </button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
