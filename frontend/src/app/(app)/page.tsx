"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";

interface AccountSummary {
  currency: string;
  cash: string;
  portfolio_value: string;
  equity: string;
  daily_pnl: string;
  daily_pnl_percent: string | null;
}

function formatMoney(value: string, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
  }).format(Number(value));
}

export default function DashboardPage() {
  const [account, setAccount] = useState<AccountSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<AccountSummary>("/api/v1/broker/account")
      .then(setAccount)
      .catch(() => setError("Connect an Alpaca paper account to load your summary."));
  }, []);

  const stats = account
    ? [
        {
          label: "Portfolio value",
          value: formatMoney(account.portfolio_value, account.currency),
          hint: "Current account value",
        },
        {
          label: "Cash",
          value: formatMoney(account.cash, account.currency),
          hint: "Available cash",
        },
        {
          label: "Equity",
          value: formatMoney(account.equity, account.currency),
          hint: "Current equity",
        },
        {
          label: "Day P&L",
          value: formatMoney(account.daily_pnl, account.currency),
          hint: account.daily_pnl_percent
            ? `${Number(account.daily_pnl_percent).toFixed(2)}% today`
            : "No prior-day baseline",
        },
      ]
    : [
        { label: "Portfolio value", value: "—", hint: "Awaiting broker data" },
        { label: "Cash", value: "—", hint: "Awaiting broker data" },
        { label: "Equity", value: "—", hint: "Awaiting broker data" },
        { label: "Day P&L", value: "—", hint: "Awaiting broker data" },
      ];

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <p className="text-sm text-muted">
          Portfolio overview and agent activity. Live paper-account metrics are
          refreshed from Alpaca and persisted for account history.
        </p>
      </section>

      {error && (
        <p className="rounded-lg border border-border bg-surface-muted p-3 text-sm text-muted">
          {error}
        </p>
      )}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="rounded-xl border border-border bg-surface p-5"
          >
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              {stat.label}
            </p>
            <p className="mt-2 text-2xl font-semibold">{stat.value}</p>
            <p className="mt-1 text-xs text-muted">{stat.hint}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-border bg-surface p-5 lg:col-span-2">
          <h3 className="text-sm font-semibold">Equity curve</h3>
          <div className="mt-4 flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
            Chart placeholder
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface p-5">
          <h3 className="text-sm font-semibold">Recent decisions</h3>
          <div className="mt-4 flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
            Timeline placeholder
          </div>
        </div>
      </section>
    </div>
  );
}
