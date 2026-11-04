"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAgentConfig,
  updateAgentConfig,
  type AgentConfig,
  type AgentConfigUpdate,
} from "@/lib/agent/agent-config-api";
import {
  AUTO_WARNING,
  NO_AGENT_MESSAGE,
  SAVED_MESSAGE,
  approvalModeOptions,
  buildConfigPatch,
  describeAgentConfigError,
  describeApprovalMode,
  formatFraction,
  isAutoMode,
  isNoAgentError,
  normalizeSymbol,
  summarizePreview,
  validateSymbol,
  validateWatchlist,
  type SymbolPreview,
} from "@/lib/agent/agent-config-view";
import { getMetaConstants } from "@/lib/meta/meta-api";
import { getBars, getSnapshot } from "@/lib/market-data/market-data-api";

type Status = "loading" | "ready" | "no-agent" | "error";
type Feedback = { tone: "success" | "error"; message: string };
type Preview = { symbol: string; loading: true } | ({ loading: false } & SymbolPreview);

const inputClass =
  "mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50";
const secondaryButton =
  "rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50";
const primaryButton =
  "rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50";

/**
 * Settings "Agent configuration" card (#141): edit the caller's own agent watchlist and
 * approval mode. It only PATCHes /agent/config — it never places orders, and the risk
 * policies still run on every proposal. `canEdit` mirrors the backend's operator gate;
 * the server's 403 stays authoritative.
 */
export function AgentConfigCard({ canEdit }: { canEdit: boolean }) {
  const [status, setStatus] = useState<Status>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [modes, setModes] = useState<string[]>([]);
  const [maxSize, setMaxSize] = useState<number | null>(null);
  const [draftWatchlist, setDraftWatchlist] = useState<string[]>([]);
  const [draftMode, setDraftMode] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmingAuto, setConfirmingAuto] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const previewRequest = useRef(0);

  const applyConfig = useCallback((cfg: AgentConfig) => {
    setConfig(cfg);
    setDraftWatchlist(cfg.watchlist);
    setDraftMode(cfg.approval_mode);
  }, []);

  const load = useCallback(async () => {
    setStatus("loading");
    setLoadError(null);
    // allSettled: constants are optional (the select falls back to the current mode).
    const [cfg, meta] = await Promise.allSettled([getAgentConfig(), getMetaConstants()]);
    const constants = meta.status === "fulfilled" ? meta.value : null;
    setMaxSize(constants?.max_watchlist_size ?? null);
    if (cfg.status === "fulfilled") {
      applyConfig(cfg.value);
      setModes(approvalModeOptions(constants?.approval_mode, cfg.value.approval_mode));
      setStatus("ready");
    } else if (isNoAgentError(cfg.reason)) {
      setStatus("no-agent");
    } else {
      setLoadError(describeAgentConfigError(cfg.reason, "load"));
      setStatus("error");
    }
  }, [applyConfig]);

  useEffect(() => {
    void load();
  }, [load]);

  const symbolError = symbolInput.trim() ? validateSymbol(symbolInput, draftWatchlist, maxSize) : null;
  const watchlistError = validateWatchlist(draftWatchlist, maxSize);
  const patch: AgentConfigUpdate = config
    ? buildConfigPatch(config, { watchlist: draftWatchlist, approval_mode: draftMode })
    : {};
  const dirty = Object.keys(patch).length > 0;
  const canSave = canEdit && !saving && dirty && !watchlistError;
  const locked = !canEdit || saving;

  function edited() {
    setFeedback(null);
    setConfirmingAuto(false);
  }

  function onSymbolChange(value: string) {
    setSymbolInput(value);
    setPreview(null);
  }

  async function onPreview(event?: React.FormEvent) {
    event?.preventDefault();
    const symbol = normalizeSymbol(symbolInput);
    if (locked || validateSymbol(symbol, draftWatchlist, maxSize)) return;
    const request = ++previewRequest.current;
    setPreview({ symbol, loading: true });
    const [snapshot, bars] = await Promise.allSettled([
      getSnapshot(symbol),
      getBars({ symbols: [symbol], limit: 1, lookbackDays: 7 }),
    ]);
    // Ignore a stale answer if the input changed while it was loading.
    if (request !== previewRequest.current) return;
    setPreview({ loading: false, ...summarizePreview(symbol, snapshot, bars) });
  }

  function onAdd(symbol: string) {
    if (validateSymbol(symbol, draftWatchlist, maxSize)) return;
    setDraftWatchlist([...draftWatchlist, symbol]);
    setSymbolInput("");
    setPreview(null);
    previewRequest.current++;
    edited();
  }

  function onRemove(symbol: string) {
    setDraftWatchlist(draftWatchlist.filter((s) => s !== symbol));
    edited();
  }

  function onReset() {
    if (config) applyConfig(config);
    edited();
  }

  function onSave() {
    if (!canSave) return;
    setFeedback(null);
    // Switching INTO AUTO removes human review, so it needs an explicit second step.
    if (patch.approval_mode !== undefined && isAutoMode(patch.approval_mode)) {
      setConfirmingAuto(true);
      return;
    }
    void save(patch);
  }

  async function save(update: AgentConfigUpdate) {
    setSaving(true);
    setFeedback(null);
    const [res] = await Promise.allSettled([updateAgentConfig(update)]);
    if (res.status === "fulfilled") {
      applyConfig(res.value);
      setFeedback({ tone: "success", message: SAVED_MESSAGE });
    } else {
      setFeedback({ tone: "error", message: describeAgentConfigError(res.reason, "save") });
    }
    setConfirmingAuto(false);
    setSaving(false);
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h3 className="text-sm font-semibold">Agent configuration</h3>
      <p className="mt-1 text-sm text-muted">
        Which symbols your agent evaluates and how much human review its trades get. Changes
        apply from the next cycle; the risk policies always run.
      </p>

      {status === "loading" ? (
        <p className="mt-2 text-sm text-muted">Loading agent configuration...</p>
      ) : status === "error" ? (
        <div className="mt-3 space-y-3">
          <div
            role="alert"
            className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
          >
            {loadError}
          </div>
          <button type="button" onClick={() => void load()} className={secondaryButton}>
            Retry
          </button>
        </div>
      ) : status === "no-agent" || !config ? (
        <div className="mt-3 flex h-40 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-surface px-4 text-center text-sm text-muted">
          <p>{NO_AGENT_MESSAGE}</p>
          <button type="button" onClick={() => void load()} className={secondaryButton}>
            Check again
          </button>
        </div>
      ) : (
        <div className="mt-3 space-y-5">
          {!canEdit && (
            <p className="text-sm text-muted">
              Only operators and admins can change the agent configuration.
            </p>
          )}

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Watchlist{maxSize !== null ? ` (${draftWatchlist.length}/${maxSize})` : ""}
            </h4>
            {draftWatchlist.length > 0 ? (
              <ul className="mt-2 flex flex-wrap gap-2" aria-label="Watchlist">
                {draftWatchlist.map((symbol) => (
                  <li
                    key={symbol}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-muted py-1 pl-3 pr-1 text-sm font-medium"
                  >
                    {symbol}
                    <button
                      type="button"
                      aria-label={`Remove ${symbol}`}
                      disabled={locked}
                      onClick={() => onRemove(symbol)}
                      className="rounded-full px-2 text-muted hover:bg-surface hover:text-negative disabled:opacity-50"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {watchlistError && <p className="mt-2 text-sm text-negative">{watchlistError}</p>}

            <form onSubmit={(e) => void onPreview(e)} className="mt-3">
              <label htmlFor="agent-add-symbol" className="text-xs font-medium text-muted">
                Add symbol
              </label>
              <div className="flex flex-wrap items-start gap-3">
                <input
                  id="agent-add-symbol"
                  value={symbolInput}
                  onChange={(e) => onSymbolChange(e.target.value)}
                  autoComplete="off"
                  disabled={locked}
                  className={`${inputClass} max-w-40 uppercase`}
                />
                <button
                  type="submit"
                  disabled={locked || !symbolInput.trim() || !!symbolError || preview?.loading}
                  className={`mt-1 ${secondaryButton}`}
                >
                  {preview?.loading ? "Loading..." : "Preview"}
                </button>
              </div>
              {symbolError && <p className="mt-1 text-sm text-negative">{symbolError}</p>}
            </form>

            {preview && (
              <div className="mt-3 rounded-lg border border-border bg-background p-3 text-sm">
                {preview.loading ? (
                  <p className="text-muted">Loading {preview.symbol} market data...</p>
                ) : (
                  <div className="space-y-2">
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                      <PreviewStat label="Latest close" value={preview.latestClose} />
                      <PreviewStat label="RSI-14" value={preview.rsi14} />
                      <PreviewStat label="SMA-20" value={preview.sma20} />
                      <PreviewStat label="SMA-50" value={preview.sma50} />
                    </dl>
                    {preview.warning && <p className="text-negative">{preview.warning}</p>}
                    {preview.note && <p className="text-muted">{preview.note}</p>}
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => onAdd(preview.symbol)}
                      className={primaryButton}
                    >
                      Add {preview.symbol}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          <div>
            <label htmlFor="agent-approval-mode" className="text-xs font-medium text-muted">
              Approval mode
            </label>
            <select
              id="agent-approval-mode"
              value={draftMode}
              disabled={locked}
              onChange={(e) => {
                setDraftMode(e.target.value);
                edited();
              }}
              className={`${inputClass} max-w-xs`}
            >
              {modes.map((mode) => (
                <option key={mode} value={mode}>
                  {describeApprovalMode(mode).label}
                </option>
              ))}
            </select>
            {isAutoMode(draftMode) ? (
              <p className="mt-2 rounded-lg border border-negative/40 bg-negative/5 px-4 py-3 text-sm text-negative">
                {AUTO_WARNING}
              </p>
            ) : (
              describeApprovalMode(draftMode).help && (
                <p className="mt-1 text-sm text-muted">{describeApprovalMode(draftMode).help}</p>
              )
            )}
          </div>

          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div className="sm:col-span-2">
              <dt className="text-xs font-medium text-muted">Cadence</dt>
              <dd>Set by the run interval in Autonomous schedule below.</dd>
            </div>
            <ReadOnlyLimit label="Max per symbol" value={config.max_symbol_allocation} />
            <ReadOnlyLimit label="Target allocation" value={config.target_allocation} />
            <ReadOnlyLimit label="Cash reserve" value={config.cash_reserve} />
            <ReadOnlyLimit label="Daily loss limit" value={config.daily_loss_limit} />
          </dl>
          <p className="text-xs text-muted">
            Risk limits are read-only here and are enforced server-side by the risk policies.
          </p>

          {feedback && (
            <p
              role={feedback.tone === "error" ? "alert" : "status"}
              className={`text-sm ${feedback.tone === "error" ? "text-negative" : "text-positive"}`}
            >
              {feedback.message}
            </p>
          )}

          {confirmingAuto ? (
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm">
                Switch to <strong>AUTO</strong>? Trades will execute without waiting for approval.
              </span>
              <button
                type="button"
                disabled={saving}
                onClick={() => void save(patch)}
                className="rounded-lg bg-negative px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                {saving ? "Saving..." : "Confirm AUTO"}
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => setConfirmingAuto(false)}
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-3">
              <button type="button" disabled={!canSave} onClick={onSave} className={primaryButton}>
                {saving ? "Saving..." : "Save"}
              </button>
              <button
                type="button"
                disabled={locked || !dirty}
                onClick={onReset}
                className={secondaryButton}
              >
                Reset
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function PreviewStat({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-medium">{value ?? "—"}</dd>
    </div>
  );
}

function ReadOnlyLimit({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd>{formatFraction(value)}</dd>
    </div>
  );
}
