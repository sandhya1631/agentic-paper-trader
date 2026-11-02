"use client";

import { useCallback, useEffect, useState } from "react";
import {
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

interface PositionsState {
  loading: boolean;
  notConnected: boolean;
  error: string | null;
  positions: AlpacaPosition[] | null;
  orders: AlpacaOrder[] | null;
}

const INITIAL: PositionsState = {
  loading: true,
  notConnected: false,
  error: null,
  positions: null,
  orders: null,
};

export function PositionsView() {
  const [state, setState] = useState<PositionsState>(INITIAL);

  const load = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true }));
    const [positions, orders] = await Promise.allSettled([getPositions(), getOrders("open")]);

    if (positions.status === "rejected" && isAccountNotConnected(positions.reason)) {
      setState({ ...INITIAL, loading: false, notConnected: true });
      return;
    }
    setState({
      loading: false,
      notConnected: false,
      error:
        positions.status === "rejected"
          ? getErrorMessage(positions.reason, "Could not load positions.")
          : null,
      positions: positions.status === "fulfilled" ? positions.value : null,
      orders: orders.status === "fulfilled" ? orders.value : null,
    });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.loading) return <p className="text-sm text-muted">Loading positions...</p>;
  if (state.notConnected) return <ConnectAccountNotice />;

  return (
    <div className="space-y-8">
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
