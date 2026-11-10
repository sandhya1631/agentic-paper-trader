"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createOrder,
  getOrders,
  getPositions,
  isAccountNotConnected,
  type AlpacaOrder,
  type AlpacaPosition,
} from "@/lib/broker/broker-api";
import { getErrorMessage } from "@/lib/api/error-message";
import { formatDateTime, formatMoney, formatPercent, formatSignedMoney, signTone } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { ConnectAccountNotice } from "@/components/ui/connect-account-notice";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { usePolling } from "@/lib/hooks/use-polling";
import { keepLastGood, shouldStartPoll, staleDataMessage } from "@/lib/live-refresh";
import { ManualOrderTicket, describeOrderError } from "./manual-order-ticket";

const REFRESH_SECONDS = 15;

interface PositionsState {
  /** True only until the first load completes; later refreshes update in place (#164). */
  loading: boolean;
  notConnected: boolean;
  error: string | null;
  positions: AlpacaPosition[] | null;
  orders: AlpacaOrder[] | null;
  /** When the data on screen was last fully refreshed (epoch ms). */
  updatedAt: number | null;
  /** The last refresh failed, so older data is still on screen. */
  stale: boolean;
}

const INITIAL: PositionsState = {
  loading: true,
  notConnected: false,
  error: null,
  positions: null,
  orders: null,
  updatedAt: null,
  stale: false,
};

export function PositionsView() {
  const [state, setState] = useState<PositionsState>(INITIAL);
  const [live, setLive] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const latestLoad = useRef(0);
  // When the outstanding refresh started (null when none): a poll tick skips while it is
  // younger than the age limit, so a refresh slower than the interval still lands, and
  // supersedes it after that, so a request that never settles cannot stop the polling.
  const inFlightSince = useRef<number | null>(null);

  // Refreshes (the poll, or the reload after a manual order or Close) keep the page
  // mounted: values are swapped in place, so the ticket and Close confirmations survive.
  const load = useCallback(async () => {
    const request = ++latestLoad.current;
    inFlightSince.current = Date.now();
    setRefreshing(true);
    const [positions, orders] = await Promise.allSettled([getPositions(), getOrders("open")]);
    // A newer refresh (an explicit reload after a manual order or Close) supersedes this one.
    if (request !== latestLoad.current) return;
    inFlightSince.current = null;
    setRefreshing(false);
    const now = Date.now();

    setState((prev) => {
      if (positions.status === "rejected" && isAccountNotConnected(positions.reason)) {
        return { ...INITIAL, loading: false, notConnected: true, updatedAt: now };
      }
      // A part that fails to refresh keeps its last good value instead of blanking.
      const nextPositions = keepLastGood(positions, prev.positions);
      const nextOrders = keepLastGood(orders, prev.orders);
      const stale = nextPositions.kept || nextOrders.kept;
      return {
        loading: false,
        notConnected: false,
        error:
          positions.status === "rejected" && !nextPositions.kept
            ? getErrorMessage(positions.reason, "Could not load positions.")
            : null,
        positions: nextPositions.value,
        orders: nextOrders.value,
        updatedAt: stale ? prev.updatedAt : now,
        stale,
      };
    });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(
    () => {
      if (shouldStartPoll(inFlightSince.current, Date.now())) void load();
    },
    REFRESH_SECONDS * 1000,
    live,
  );

  if (state.loading) return <p className="text-sm text-muted">Loading positions...</p>;
  if (state.notConnected) return <ConnectAccountNotice />;

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Positions &amp; orders</h2>
          <p className="text-sm text-muted">Open positions and orders, refreshing live.</p>
          {state.stale && (
            <p role="status" className="mt-1 text-xs text-negative">
              {staleDataMessage(state.updatedAt)}
            </p>
          )}
        </div>
        <AutoRefreshToggle
          enabled={live}
          onChange={setLive}
          seconds={REFRESH_SECONDS}
          updating={refreshing}
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Open positions</h2>
        {state.error ? (
          <div
            role="alert"
            className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
          >
            {state.error}
          </div>
        ) : state.positions && state.positions.length > 0 ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Qty</th>
                  <th className="px-4 py-3">Avg entry</th>
                  <th className="px-4 py-3">Current</th>
                  <th className="px-4 py-3">Market value</th>
                  <th className="px-4 py-3">Unrealized P/L</th>
                  <th className="px-4 py-3">Manual</th>
                </tr>
              </thead>
              <tbody>
                {state.positions.map((p) => (
                  <tr key={p.symbol} className="border-t border-border">
                    <td className="px-4 py-3 font-medium">{p.symbol}</td>
                    <td className="px-4 py-3">{p.qty}</td>
                    <td className="px-4 py-3">{formatMoney(p.avg_entry_price)}</td>
                    <td className="px-4 py-3">{formatMoney(p.current_price)}</td>
                    <td className="px-4 py-3">{formatMoney(p.market_value)}</td>
                    <td className="px-4 py-3">
                      <Badge tone={signTone(p.unrealized_pl)}>
                        {formatSignedMoney(p.unrealized_pl)} ({formatPercent(p.unrealized_plpc)})
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <ClosePositionButton position={p} onClosed={() => void load()} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyRow>No open positions.</EmptyRow>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Open orders</h2>
        {state.orders && state.orders.length > 0 ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Side</th>
                  <th className="px-4 py-3">Qty</th>
                  <th className="px-4 py-3">Filled</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {state.orders.map((o) => (
                  <tr key={o.id} className="border-t border-border">
                    <td className="px-4 py-3 font-medium">{o.symbol}</td>
                    <td className="px-4 py-3">
                      <Badge tone={o.side.toUpperCase() === "BUY" ? "positive" : "negative"}>
                        {o.side.toUpperCase()}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">{o.qty}</td>
                    <td className="px-4 py-3">{o.filled_qty}</td>
                    <td className="px-4 py-3">{o.status}</td>
                    <td className="px-4 py-3 text-muted">{formatDateTime(o.submitted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyRow>No open orders.</EmptyRow>
        )}
      </section>

      <ManualOrderTicket onSubmitted={() => void load()} />
    </div>
  );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-24 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
      {children}
    </div>
  );
}

/**
 * Per-position manual close (#143). Submits a MARKET order on the opposite side
 * for the full quantity — a manual override that bypasses the risk policies, so
 * it is gated behind an inline confirm. Handles 423 (kill switch) / 502 (broker).
 */
function ClosePositionButton({
  position,
  onClosed,
}: {
  position: AlpacaPosition;
  onClosed: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const qty = Math.floor(Math.abs(Number(position.qty)));
  const side = position.side.toUpperCase() === "SHORT" ? "BUY" : "SELL";

  async function close() {
    setBusy(true);
    setError(null);
    const [res] = await Promise.allSettled([
      createOrder({ symbol: position.symbol, qty, side, type: "MARKET", time_in_force: "DAY" }),
    ]);
    if (res.status === "fulfilled") {
      setConfirming(false);
      onClosed();
    } else {
      setError(describeOrderError(res.reason));
    }
    setBusy(false);
  }

  if (!Number.isInteger(qty) || qty <= 0) {
    return <span className="text-xs text-muted">—</span>;
  }

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="rounded-lg border border-border px-3 py-1 text-xs font-medium hover:bg-surface-muted"
      >
        Close
      </button>
    );
  }

  return (
    <span className="flex flex-col gap-1">
      <span className="flex items-center gap-2">
        <span className="text-xs text-negative">{side} {qty} (override)?</span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void close()}
          className="rounded-lg bg-negative px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy ? "..." : "Confirm"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => setConfirming(false)}
          className="rounded-lg border border-border px-2 py-1 text-xs hover:bg-surface-muted"
        >
          Cancel
        </button>
      </span>
      {error ? <span role="alert" className="text-xs text-negative">{error}</span> : null}
    </span>
  );
}
