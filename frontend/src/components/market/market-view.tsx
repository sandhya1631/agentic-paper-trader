"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getBars,
  getSnapshot,
  type MarketSnapshot,
  type OHLCVBar,
} from "@/lib/market-data/market-data-api";
import { getAgentConfig } from "@/lib/agent/agent-config-api";
import { getConstants } from "@/lib/meta/meta-api";
import {
  EMPTY_WATCHLIST_MESSAGE,
  resolveMarketWatchlist,
  selectSymbol,
  type MarketWatchlist,
} from "@/lib/market-data/market-watchlist";
import {
  ageSeconds,
  estimatedMarketStatus,
  formatAge,
  freshness,
  latestBarTimestamp,
  STALE_THRESHOLD_SECONDS,
  type Freshness,
} from "@/lib/market-data/freshness";
import { isAccountNotConnected } from "@/lib/broker/broker-api";
import { getErrorMessage } from "@/lib/api/error-message";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { PriceChart } from "./price-chart";

interface MarketState {
  loading: boolean;
  bars: OHLCVBar[] | null;
  barsError: string | null;
  snapshot: MarketSnapshot | null;
  snapshotError: string | null;
}

const INITIAL: MarketState = {
  loading: true,
  bars: null,
  barsError: null,
  snapshot: null,
  snapshotError: null,
};

const freshnessTone: Record<Freshness, "positive" | "negative" | "neutral"> = {
  FRESH: "positive",
  STALE: "negative",
  UNKNOWN: "neutral",
};

export function MarketView() {
  // null until the watchlist has loaded the first time (the tab row shows a placeholder).
  const [watchlist, setWatchlist] = useState<MarketWatchlist | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  // Bumped on every watchlist (re)load, so Refresh also reloads the selected symbol's data.
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState<MarketState>(INITIAL);
  const watchlistRequest = useRef(0);
  const dataRequest = useRef(0);

  const loadWatchlist = useCallback(async () => {
    const request = ++watchlistRequest.current;
    const [config, constants] = await Promise.allSettled([getAgentConfig(), getConstants()]);
    if (request !== watchlistRequest.current) return; // a newer Refresh superseded this one
    const next = resolveMarketWatchlist(config, constants);
    if (next.symbols.length === 0) {
      // No symbol left to select: drop the previous symbol's bars and snapshot, and
      // ignore any load for it still in flight.
      dataRequest.current++;
      setState(INITIAL);
    }
    setWatchlist(next);
    setSymbol((current) => selectSymbol(next.symbols, current));
    setReloadKey((key) => key + 1);
  }, []);

  const load = useCallback(async (sym: string) => {
    const request = ++dataRequest.current;
    setState((prev) => ({ ...prev, loading: true }));
    const [bars, snapshot] = await Promise.allSettled([
      getBars({ symbols: [sym], limit: 90, lookbackDays: 90 }),
      getSnapshot(sym),
    ]);
    if (request !== dataRequest.current) return; // another symbol was selected meanwhile

    setState({
      loading: false,
      bars: bars.status === "fulfilled" ? bars.value[sym] ?? [] : null,
      barsError:
        bars.status === "rejected"
          ? getErrorMessage(bars.reason, "Could not load price bars.")
          : null,
      snapshot: snapshot.status === "fulfilled" ? snapshot.value : null,
      snapshotError:
        snapshot.status === "rejected"
          ? isAccountNotConnected(snapshot.reason)
            ? "Connect an Alpaca paper account in Settings to see the live snapshot."
            : getErrorMessage(snapshot.reason, "Could not load the snapshot.")
          : null,
    });
  }, []);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  useEffect(() => {
    if (symbol !== null) void load(symbol);
  }, [load, symbol, reloadKey]);

  const latestTs = latestBarTimestamp(state.bars);
  const age = ageSeconds(latestTs, Date.now());
  const fresh = freshness(age);
  const market = estimatedMarketStatus(new Date());

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Market data</h2>
          <p className="text-sm text-muted">
            Price and indicator charts for the tracked symbols, with a live snapshot and a
            data-freshness check (the agent denies trades on data older than{" "}
            {STALE_THRESHOLD_SECONDS}s).
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadWatchlist()}
          className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
        >
          Refresh
        </button>
      </section>

      {watchlist === null ? (
        <p role="status" className="py-1.5 text-sm text-muted">
          Loading watchlist...
        </p>
      ) : (
        <div className="space-y-2">
          {watchlist.symbols.length > 0 && (
            <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Symbol">
              {watchlist.symbols.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="tab"
                  aria-selected={s === symbol}
                  onClick={() => setSymbol(s)}
                  className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                    s === symbol
                      ? "border-brand bg-brand/10 text-brand"
                      : "border-border hover:bg-surface-muted"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          {watchlist.error && (
            <p role="alert" className="text-sm text-negative">
              {watchlist.error}
            </p>
          )}
        </div>
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-5 py-3 text-sm">
        <span className="flex items-center gap-2">
          <span className="font-medium">Data</span>
          <Badge tone={freshnessTone[fresh]}>
            {fresh === "UNKNOWN" ? "No data" : fresh}
          </Badge>
        </span>
        <span className="text-muted">
          Latest bar: {formatDateTime(latestTs)}
          {age !== null ? ` (${formatAge(age)} ago)` : ""}
        </span>
        <span className="flex items-center gap-2">
          <span className="font-medium">Market</span>
          <Badge tone={market === "OPEN" ? "positive" : "neutral"}>{market}</Badge>
          <span className="text-muted">(estimated, ET hours)</span>
        </span>
        {fresh === "STALE" && (
          <span className="text-negative">
            Data is stale — the agent&apos;s freshness policy (P-004) will deny trades until it
            refreshes.
          </span>
        )}
      </section>

      {symbol === null ? (
        watchlist !== null &&
        watchlist.source === "agent" && (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-border bg-surface px-4 text-center text-sm text-muted">
            {EMPTY_WATCHLIST_MESSAGE}
          </div>
        )
      ) : state.loading ? (
        <p className="text-sm text-muted">Loading {symbol} market data...</p>
      ) : (
        <>
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">Snapshot</h3>
            {state.snapshotError ? (
              <div
                role="alert"
                className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
              >
                {state.snapshotError}
              </div>
            ) : state.snapshot ? (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard label="Latest close" value={fmt(state.snapshot.latest_close)} />
                <StatCard label="RSI-14" value={fmt(state.snapshot.rsi_14)} />
                <StatCard label="SMA-20" value={fmt(state.snapshot.sma_20)} />
                <StatCard label="SMA-50" value={fmt(state.snapshot.sma_50)} />
              </div>
            ) : (
              <p className="text-sm text-muted">No snapshot available.</p>
            )}
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-surface p-5">
            <h3 className="text-sm font-semibold">{symbol} price &amp; indicators</h3>
            {state.barsError ? (
              <div
                role="alert"
                className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
              >
                {state.barsError}
              </div>
            ) : state.bars && state.bars.length > 0 ? (
              <PriceChart symbol={symbol} bars={state.bars} />
            ) : (
              <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
                No price bars available for {symbol}.
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

/** Snapshot values are Decimal-as-string; render the raw value or an em dash. */
function fmt(value: string | null): string {
  return value === null || value === "" ? "—" : value;
}
