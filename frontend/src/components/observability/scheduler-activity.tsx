"use client";

import { useCallback, useEffect, useState } from "react";
import { getSchedulerLogs, type SchedulerTickLog } from "@/lib/agent/observability-api";
import { describeTick } from "@/lib/agent/scheduler-log-view";
import { listCycles, type AgentCycle } from "@/lib/agent/cycles-api";
import { getErrorMessage } from "@/lib/api/error-message";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { usePolling } from "@/lib/hooks/use-polling";

const REFRESH_SECONDS = 15;

interface ActivityState {
  loading: boolean;
  logs: SchedulerTickLog[] | null;
  logsError: string | null;
  cycles: AgentCycle[] | null;
}

const INITIAL: ActivityState = { loading: true, logs: null, logsError: null, cycles: null };

/** Scheduler tick history + recent cycles, auto-refreshing (#143). */
export function SchedulerActivity() {
  const [state, setState] = useState<ActivityState>(INITIAL);
  const [live, setLive] = useState(true);

  const load = useCallback(async () => {
    const [logs, cycles] = await Promise.allSettled([getSchedulerLogs(), listCycles({ limit: 5 })]);
    setState({
      loading: false,
      logs: logs.status === "fulfilled" ? logs.value : null,
      logsError:
        logs.status === "rejected"
          ? getErrorMessage(logs.reason, "Could not load scheduler activity.")
          : null,
      cycles: cycles.status === "fulfilled" ? cycles.value.items : null,
    });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(() => void load(), REFRESH_SECONDS * 1000, live);

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Scheduler activity</h3>
          <p className="mt-1 text-sm text-muted">
            Recent scheduled ticks and agent cycles. Ticks only run while a Celery worker is
            processing the queue.
          </p>
        </div>
        <AutoRefreshToggle enabled={live} onChange={setLive} seconds={REFRESH_SECONDS} />
      </div>

      {state.loading ? (
        <p className="mt-3 text-sm text-muted">Loading scheduler activity...</p>
      ) : state.logsError ? (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
        >
          {state.logsError}
        </div>
      ) : (
        <>
          <h4 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">
            Recent ticks
          </h4>
          {state.logs && state.logs.length > 0 ? (
            <ul className="mt-2 divide-y divide-border">
              {state.logs.map((log, i) => {
                const view = describeTick(log.tick);
                const t = log.tick;
                return (
                  <li
                    key={`${log.schedule_id ?? "global"}-${t.started_at ?? i}`}
                    className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <Badge tone={view.tone}>{view.label}</Badge>
                      {view.reason ? <span className="text-muted">{view.reason}</span> : null}
                    </span>
                    <span className="flex items-center gap-3 text-xs text-muted">
                      {typeof t.successful_agents === "number" ? (
                        <span>
                          {t.successful_agents}/{t.attempted_agents ?? 0} ok
                          {t.failed_agents ? `, ${t.failed_agents} failed` : ""}
                        </span>
                      ) : null}
                      <span>{formatDateTime(t.started_at)}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted">
              No scheduler ticks recorded yet. Start a schedule above and ensure a Celery worker is
              running.
            </p>
          )}

          <h4 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted">
            Recent cycles
          </h4>
          {state.cycles && state.cycles.length > 0 ? (
            <ul className="mt-2 divide-y divide-border">
              {state.cycles.map((cycle) => (
                <li
                  key={cycle.id}
                  className="flex items-center justify-between gap-3 py-2 text-sm"
                >
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
          ) : (
            <p className="mt-2 text-sm text-muted">No agent cycles recorded yet.</p>
          )}
        </>
      )}
    </section>
  );
}
