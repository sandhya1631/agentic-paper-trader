"use client";

import { useEffect, useState } from "react";
import { listStrategies, type Strategy } from "@/lib/agent/agent-config-api";
import { findStrategy, strategySelectOptions } from "@/lib/agent/agent-config-view";
import { getBars } from "@/lib/market-data/market-data-api";
import {
  buildReplaySample,
  describeReplayError,
  formatPercentPoints,
  validateReplaySymbol,
} from "@/lib/agent/replay-view";
import { runReplay, type ReplayMetrics } from "@/lib/agent/replay-api";

const inputClass =
  "mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50";
const primaryButton =
  "rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50";

const SAMPLE_BAR_LIMIT = 100;

/**
 * Settings "Strategy replay" card (#194): score a strategy's decision against the most
 * recent bars for one symbol, walk-forward (no look-ahead). It is read-only — it never
 * creates cycles, reads the broker, or submits orders; `POST /agent/replay` is the only
 * call it makes. `canRun` mirrors the backend's operator gate.
 */
export function ReplayCard({ canRun }: { canRun: boolean }) {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategiesError, setStrategiesError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [running, setRunning] = useState(false);
  const [metrics, setMetrics] = useState<ReplayMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listStrategies()
      .then((list) => {
        setStrategies(list);
        setStrategiesError(null);
        setStrategy((current) => current || list[0]?.version || "");
      })
      .catch((e) => {
        setStrategies([]);
        setStrategiesError(describeReplayError(e, "load"));
      });
  }, []);

  const symbolError = symbolInput.trim() ? validateReplaySymbol(symbolInput) : null;
  const locked = !canRun || running;
  const canSubmit = canRun && !running && !!strategy && !!symbolInput.trim() && !symbolError;
  const strategyOptions = strategySelectOptions(strategies, strategy);
  const selectedStrategy = findStrategy(strategyOptions, strategy);

  async function onRun(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    const symbol = symbolInput.trim().toUpperCase();
    setRunning(true);
    setError(null);
    setMetrics(null);
    try {
      const barsBySymbol = await getBars({ symbols: [symbol], limit: SAMPLE_BAR_LIMIT });
      const built = buildReplaySample(symbol, barsBySymbol[symbol] ?? []);
      if ("error" in built) {
        setError(built.error);
        return;
      }
      setMetrics(await runReplay({ strategy_version: strategy, samples: [built.sample] }));
    } catch (e) {
      setError(describeReplayError(e, "run"));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h3 className="text-sm font-semibold">Strategy replay</h3>
      <p className="mt-1 text-sm text-muted">
        See how a strategy would have decided on a symbol&apos;s most recent bars, scored
        against what actually happened next. This never places an order or creates a cycle.
      </p>

      {!canRun && (
        <p className="mt-2 text-sm text-muted">
          Only operators and admins can run a strategy replay.
        </p>
      )}

      <form onSubmit={(e) => void onRun(e)} className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="replay-strategy" className="text-xs font-medium text-muted">
            Strategy
          </label>
          <select
            id="replay-strategy"
            value={strategy}
            disabled={locked}
            onChange={(e) => setStrategy(e.target.value)}
            className={`${inputClass} max-w-xs`}
          >
            {strategyOptions.map((s) => (
              <option key={s.version} value={s.version}>
                {s.name || s.version}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="replay-symbol" className="text-xs font-medium text-muted">
            Symbol
          </label>
          <input
            id="replay-symbol"
            value={symbolInput}
            onChange={(e) => {
              setSymbolInput(e.target.value);
              setError(null);
            }}
            autoComplete="off"
            disabled={locked}
            className={`${inputClass} max-w-32 uppercase`}
          />
        </div>
        <button type="submit" disabled={!canSubmit} className={primaryButton}>
          {running ? "Running..." : "Run replay"}
        </button>
      </form>
      {symbolInput.trim() && symbolError && (
        <p className="mt-1 text-sm text-negative">{symbolError}</p>
      )}
      {selectedStrategy?.summary && (
        <p className="mt-1 text-sm text-muted">{selectedStrategy.summary}</p>
      )}
      {strategiesError && <p className="mt-1 text-sm text-negative">{strategiesError}</p>}

      {error && (
        <p role="alert" className="mt-3 text-sm text-negative">
          {error}
        </p>
      )}

      {metrics && (
        <dl
          role="status"
          className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-border bg-background p-4 text-sm sm:grid-cols-4"
        >
          <Metric label="Decisions" value={`${metrics.valid_decisions} valid / ${metrics.invalid_decisions} invalid`} />
          <Metric
            label="Actions"
            value={`${metrics.action_counts.BUY ?? 0} BUY · ${metrics.action_counts.SELL ?? 0} SELL · ${
              metrics.action_counts.HOLD ?? 0
            } HOLD`}
          />
          <Metric label="Avg. input tokens" value={Math.round(metrics.average_input_tokens).toLocaleString()} />
          <Metric label="Avg. latency" value={`${Math.round(metrics.average_latency_ms)} ms`} />
          <Metric label="Decision return" value={formatPercentPoints(metrics.cumulative_return_pct)} />
          <Metric label="Buy & hold return" value={formatPercentPoints(metrics.buy_and_hold_return_pct)} />
        </dl>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
