"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  getAccount,
  getPositions,
  isAccountNotConnected,
  type AlpacaAccount,
  type AlpacaPosition,
} from "@/lib/broker/broker-api";
import { listCycles, type AgentCycle } from "@/lib/agent/cycles-api";
import { getSchedulerStatus, type SchedulerStatus } from "@/lib/agent/scheduler-api";
import { getErrorMessage } from "@/lib/api/error-message";
import {
  formatDateTime,
  formatMoney,
  formatPercent,
  formatSignedMoney,
  signTone,
  toNumber,
} from "@/lib/format";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { ConnectAccountNotice } from "@/components/ui/connect-account-notice";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { usePolling } from "@/lib/hooks/use-polling";
import { keepLastGood, shouldStartPoll, staleDataMessage } from "@/lib/live-refresh";

const REFRESH_SECONDS = 15;

interface DashboardState {
  /** True only until the first load completes; later refreshes update in place (#164). */
  loading: boolean;
  notConnected: boolean;
  accountError: string | null;
  account: AlpacaAccount | null;
  positions: AlpacaPosition[] | null;
  cycles: AgentCycle[] | null;
  scheduler: SchedulerStatus | null;
  /** When the data on screen was last fully refreshed (epoch ms). */
  updatedAt: number | null;
  /** The last refresh failed, so older data is still on screen. */
  stale: boolean;
}

const INITIAL: DashboardState = {
  loading: true,
  notConnected: false,
  accountError: null,
  account: null,
  positions: null,
  cycles: null,
  scheduler: null,
  updatedAt: null,
  stale: false,
};

export function DashboardView() {
  const [state, setState] = useState<DashboardState>(INITIAL);
  const [live, setLive] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const latestLoad = useRef(0);
  // When the outstanding refresh started (null when none): a poll tick skips while it is
  // younger than the age limit, so a refresh slower than the interval still lands, and
  // supersedes it after that, so a request that never settles cannot stop the polling.
  const inFlightSince = useRef<number | null>(null);

  // Refreshes keep the page mounted and swap the values in place (#164).
  const load = useCallback(async () => {
    const request = ++latestLoad.current;
    inFlightSince.current = Date.now();
    setRefreshing(true);
    const [account, positions, cycles, scheduler] = await Promise.allSettled([
      getAccount(),
      getPositions(),
      listCycles({ limit: 5 }),
      getSchedulerStatus(),
    ]);
    // Defensive: if a newer refresh was started meanwhile, its answer wins.
    if (request !== latestLoad.current) return;
    inFlightSince.current = null;
    setRefreshing(false);
    const now = Date.now();

    setState((prev) => {
      if (account.status === "rejected" && isAccountNotConnected(account.reason)) {
        return { ...INITIAL, loading: false, notConnected: true, updatedAt: now };
      }
      // A part that fails to refresh keeps its last good value instead of blanking.
      const cycleItems: PromiseSettledResult<AgentCycle[]> =
        cycles.status === "fulfilled" ? { status: "fulfilled", value: cycles.value.items } : cycles;
      const nextAccount = keepLastGood(account, prev.account);
      const nextPositions = keepLastGood(positions, prev.positions);
      const nextCycles = keepLastGood(cycleItems, prev.cycles);
      const nextScheduler = keepLastGood(scheduler, prev.scheduler);
      const stale = [nextAccount, nextPositions, nextCycles, nextScheduler].some((p) => p.kept);
      return {
        loading: false,
        notConnected: false,
        accountError:
          account.status === "rejected" && !nextAccount.kept
            ? getErrorMessage(account.reason, "Could not load account summary.")
            : null,
        account: nextAccount.value,
        positions: nextPositions.value,
        cycles: nextCycles.value,
        scheduler: nextScheduler.value,
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

  if (state.loading) {
    return <p className="text-sm text-muted">Loading dashboard...</p>;
  }

  if (state.notConnected) {
    return (
      <div className="space-y-6">
        <DashboardHeader />
        <ConnectAccountNotice />
      </div>
    );
  }

  const acct = state.account;
  const equity = toNumber(acct?.equity);
  const lastEquity = toNumber(acct?.last_equity);
  const dailyPnl = equity !== null && lastEquity !== null ? equity - lastEquity : null;
  const dailyPct = dailyPnl !== null && lastEquity ? dailyPnl / lastEquity : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <DashboardHeader />
        <AutoRefreshToggle
          enabled={live}
          onChange={setLive}
          seconds={REFRESH_SECONDS}
          updating={refreshing}
        />
      </div>

      {state.stale && (
        <p role="status" className="text-xs text-negative">
          {staleDataMessage(state.updatedAt)}
        </p>
      )}

      {state.accountError ? (
        <div
          role="alert"
          className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
        >
          {state.accountError}
        </div>
      ) : (
        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Portfolio value" value={formatMoney(acct?.portfolio_value)} />
          <StatCard label="Cash" value={formatMoney(acct?.cash)} />
          <StatCard label="Buying power" value={formatMoney(acct?.buying_power)} />
          <StatCard
            label="Daily P/L"
            value={formatSignedMoney(dailyPnl)}
            hint={dailyPct !== null ? formatPercent(dailyPct) : undefined}
            tone={signTone(dailyPnl)}
          />
        </section>
      )}

      <AgentStateBar scheduler={state.scheduler} positions={state.positions} />

      <section className="rounded-xl border border-border bg-surface p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Recent decisions</h3>
          <Link href="/audit" className="text-xs text-brand">
            View audit trail
          </Link>
        </div>
        {state.cycles === null ? (
          <p className="mt-4 text-sm text-muted">Cycle history is unavailable right now.</p>
        ) : state.cycles.length === 0 ? (
          <p className="mt-4 text-sm text-muted">No agent cycles yet. Trigger one from Settings.</p>
        ) : (
          <ul className="mt-4 divide-y divide-border">
            {state.cycles.map((cycle) => (
              <li key={cycle.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="text-muted">{formatDateTime(cycle.started_at)}</span>
                <span className="flex items-center gap-2">
                  {cycle.proposal_json?.action ? (
                    <span className="font-medium">
                      {cycle.proposal_json.action} {cycle.proposal_json.symbol}
                    </span>
                  ) : null}
                  <Badge tone={cycle.status === "FAILED" ? "negative" : "neutral"}>
                    {cycle.status}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DashboardHeader() {
  return (
    <section>
      <h2 className="text-lg font-semibold">Dashboard</h2>
      <p className="text-sm text-muted">
        Live portfolio exposure and recent agent activity.
      </p>
    </section>
  );
}

function AgentStateBar({
  scheduler,
  positions,
}: {
  scheduler: SchedulerStatus | null;
  positions: AlpacaPosition[] | null;
}) {
  const running = scheduler?.running ?? false;
  const activeSchedules = scheduler?.schedules.filter((s) => s.enabled).length ?? 0;
  const nextRun = scheduler?.schedules.find((s) => s.enabled)?.next_run_time ?? null;

  return (
    <section className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-5 py-3 text-sm">
      <span className="flex items-center gap-2">
        <span className="font-medium">Agent</span>
        <Badge tone={running && activeSchedules > 0 ? "positive" : "neutral"}>
          {running && activeSchedules > 0 ? "Scheduled" : "Idle"}
        </Badge>
      </span>
      {activeSchedules > 0 ? (
        <span className="text-muted">Next run: {formatDateTime(nextRun)}</span>
      ) : (
        <span className="text-muted">No active schedule — start one in Settings.</span>
      )}
      <span className="text-muted">
        Open positions: {positions === null ? "—" : positions.length}
      </span>
    </section>
  );
}
