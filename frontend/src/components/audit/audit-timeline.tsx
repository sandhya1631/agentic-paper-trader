"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getCycle,
  listCycles,
  type AgentCycle,
  type CycleListFilters,
} from "@/lib/agent/cycles-api";
import { getConstants } from "@/lib/meta/meta-api";
import { getErrorMessage } from "@/lib/api/error-message";
import { formatDateTime } from "@/lib/format";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { formatEvidence, outcomeStyle, parsePolicyResult } from "@/lib/approvals/approval-view";

const PAGE = 20;

interface AuditFilters {
  startedAfter: string;
  startedBefore: string;
  action: string;
  status: string;
  policyDecision: string;
}

const EMPTY_FILTERS: AuditFilters = {
  startedAfter: "",
  startedBefore: "",
  action: "",
  status: "",
  policyDecision: "",
};

/** Local date input (yyyy-mm-dd) -> an ISO instant for the API's date-range filter. */
function toIsoBound(localDate: string, endOfDay: boolean): string | undefined {
  if (!localDate) return undefined;
  return `${localDate}T${endOfDay ? "23:59:59.999" : "00:00:00.000"}Z`;
}

function toQueryFilters(filters: AuditFilters): CycleListFilters {
  return {
    startedAfter: toIsoBound(filters.startedAfter, false),
    startedBefore: toIsoBound(filters.startedBefore, true),
    action: filters.action ? (filters.action as CycleListFilters["action"]) : undefined,
    status: filters.status || undefined,
    policyDecision: filters.policyDecision
      ? (filters.policyDecision as CycleListFilters["policyDecision"])
      : undefined,
  };
}

function statusTone(status: string): BadgeTone {
  if (status === "COMPLETED") return "positive";
  if (status === "FAILED" || status === "REJECTED") return "negative";
  return "neutral";
}

function readSnapshot(cycle: AgentCycle): {
  market: Record<string, Record<string, unknown>>;
  executionStatus: string | null;
} {
  const snap = (cycle.snapshot_json ?? {}) as Record<string, unknown>;
  return {
    market: (snap.market as Record<string, Record<string, unknown>>) ?? {},
    executionStatus: typeof snap.execution_status === "string" ? snap.execution_status : null,
  };
}

export function AuditTimeline() {
  const [items, setItems] = useState<AgentCycle[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  const [options, setOptions] = useState<{
    actions: string[];
    statuses: string[];
    policyDecisions: string[];
  } | null>(null);

  useEffect(() => {
    getConstants()
      .then((c) =>
        setOptions({
          actions: c.decision_action,
          statuses: c.cycle_status,
          policyDecisions: c.policy_decision_type,
        }),
      )
      .catch(() => setOptions(null)); // Filters still work; dropdowns just show nothing extra.
  }, []);

  const loadFirst = useCallback(async (activeFilters: AuditFilters) => {
    try {
      const res = await listCycles({ limit: PAGE, offset: 0, ...toQueryFilters(activeFilters) });
      setItems(res.items);
      setTotal(res.total);
      setNextOffset(res.next_offset);
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e, "Could not load the decision timeline."));
    }
  }, []);

  useEffect(() => {
    void loadFirst(filters);
  }, [filters, loadFirst]);

  async function loadMore() {
    if (nextOffset == null) return;
    setLoadingMore(true);
    try {
      const res = await listCycles({
        limit: PAGE,
        offset: nextOffset,
        ...toQueryFilters(filters),
      });
      setItems((prev) => [...(prev ?? []), ...res.items]);
      setNextOffset(res.next_offset);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setLoadingMore(false);
    }
  }

  const filtersActive = Object.values(filters).some(Boolean);

  return (
    <div className="space-y-4">
      <section>
        <h2 className="text-lg font-semibold">Audit trail</h2>
        <p className="text-sm text-muted">
          Every agent cycle, newest first. Expand a cycle to see its market snapshot, the
          model&apos;s rationale, each policy result with evidence, the approval, and the broker
          outcome.
        </p>
      </section>

      <AuditFilterBar filters={filters} onChange={setFilters} options={options} />

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
        >
          {error}
        </div>
      )}

      {items === null && !error && <p className="text-sm text-muted">Loading timeline...</p>}

      {items !== null && items.length === 0 && (
        <div className="flex h-32 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
          {filtersActive ? "No cycles match these filters." : "No agent cycles recorded yet."}
        </div>
      )}

      {items !== null && items.length > 0 && (
        <p className="text-xs text-muted">
          {filtersActive ? `${total} matching cycle${total === 1 ? "" : "s"}` : `${total} total`}
        </p>
      )}

      {items?.map((cycle) => (
        <CycleRow key={cycle.id} summary={cycle} />
      ))}

      {nextOffset != null && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-surface-muted disabled:opacity-50"
        >
          {loadingMore ? "Loading..." : "Load more"}
        </button>
      )}
    </div>
  );
}

function AuditFilterBar({
  filters,
  onChange,
  options,
}: {
  filters: AuditFilters;
  onChange: (filters: AuditFilters) => void;
  options: { actions: string[]; statuses: string[]; policyDecisions: string[] } | null;
}) {
  function set<K extends keyof AuditFilters>(key: K, value: AuditFilters[K]) {
    onChange({ ...filters, [key]: value });
  }

  const hasFilters = Object.values(filters).some(Boolean);
  const selectClass =
    "rounded-lg border border-border bg-surface px-2 py-1.5 text-sm";

  return (
    <section
      aria-label="Filter the audit trail"
      className="flex flex-wrap items-end gap-3 rounded-xl border border-border bg-surface p-4"
    >
      <div className="space-y-1">
        <label htmlFor="audit-from" className="text-xs font-medium text-muted">
          From
        </label>
        <input
          id="audit-from"
          type="date"
          value={filters.startedAfter}
          onChange={(e) => set("startedAfter", e.target.value)}
          className={selectClass}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor="audit-to" className="text-xs font-medium text-muted">
          To
        </label>
        <input
          id="audit-to"
          type="date"
          value={filters.startedBefore}
          onChange={(e) => set("startedBefore", e.target.value)}
          className={selectClass}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor="audit-action" className="text-xs font-medium text-muted">
          Action
        </label>
        <select
          id="audit-action"
          value={filters.action}
          onChange={(e) => set("action", e.target.value)}
          className={selectClass}
        >
          <option value="">All</option>
          {(options?.actions ?? []).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor="audit-status" className="text-xs font-medium text-muted">
          Status
        </label>
        <select
          id="audit-status"
          value={filters.status}
          onChange={(e) => set("status", e.target.value)}
          className={selectClass}
        >
          <option value="">All</option>
          {(options?.statuses ?? []).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor="audit-policy" className="text-xs font-medium text-muted">
          Policy outcome
        </label>
        <select
          id="audit-policy"
          value={filters.policyDecision}
          onChange={(e) => set("policyDecision", e.target.value)}
          className={selectClass}
        >
          <option value="">All</option>
          {(options?.policyDecisions ?? []).map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      {hasFilters && (
        <button
          type="button"
          onClick={() => onChange(EMPTY_FILTERS)}
          className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
        >
          Clear filters
        </button>
      )}
    </section>
  );
}

function CycleRow({ summary }: { summary: AgentCycle }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<AgentCycle | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    const willOpen = !open;
    setOpen(willOpen);
    if (willOpen && detail === null && !loading) {
      setLoading(true);
      try {
        setDetail(await getCycle(summary.id));
        setDetailError(null);
      } catch (e) {
        setDetailError(getErrorMessage(e, "Could not load cycle detail."));
      } finally {
        setLoading(false);
      }
    }
  }

  const cycle = detail ?? summary;
  const action = cycle.proposal_json?.action;
  const symbol = cycle.proposal_json?.symbol;

  return (
    <article className="rounded-xl border border-border bg-surface">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center justify-between gap-3 px-5 py-4 text-left"
      >
        <span className="flex items-center gap-3">
          <span className="text-sm text-muted">{formatDateTime(cycle.started_at)}</span>
          <span className="text-xs text-muted">{cycle.trigger}</span>
          {action ? (
            <span className="font-medium">
              {action} {symbol}
            </span>
          ) : null}
        </span>
        <Badge tone={statusTone(cycle.status)}>{cycle.status}</Badge>
      </button>

      {open && (
        <div className="space-y-4 border-t border-border p-5 text-sm">
          {loading && <p className="text-muted">Loading cycle detail...</p>}
          {detailError && (
            <div role="alert" className="text-negative">
              {detailError}
            </div>
          )}
          {!loading && !detailError && <CycleDetail cycle={cycle} />}
        </div>
      )}
    </article>
  );
}

function CycleDetail({ cycle }: { cycle: AgentCycle }) {
  const { market, executionStatus } = readSnapshot(cycle);
  const policy =
    parsePolicyResult(cycle.policy_result_json) ??
    parsePolicyResult(cycle.approval?.policy_result_json ?? null);
  const policyFromApproval = !parsePolicyResult(cycle.policy_result_json) && !!policy;
  const symbols = Object.keys(market);

  return (
    <>
      {cycle.failure_code && (
        <div className="rounded-lg border border-negative/30 bg-negative/10 px-3 py-2 text-negative">
          {cycle.failure_code}
          {cycle.failure_message ? `: ${cycle.failure_message}` : ""}
        </div>
      )}

      {cycle.proposal_json?.reasoning && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Rationale</h4>
          <p className="mt-1">{cycle.proposal_json.reasoning}</p>
          {(cycle.prompt_text || cycle.llm_raw_response) && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-muted">Prompt &amp; raw model output</summary>
              {cycle.prompt_text && (
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-xs">
                  {cycle.prompt_text}
                </pre>
              )}
              {cycle.llm_raw_response && (
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-xs">
                  {cycle.llm_raw_response}
                </pre>
              )}
            </details>
          )}
        </section>
      )}

      {symbols.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Market snapshot &amp; indicators
          </h4>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="py-1 pr-3">Symbol</th>
                  <th className="py-1 pr-3">Price</th>
                  <th className="py-1 pr-3">RSI-14</th>
                  <th className="py-1 pr-3">SMA-20</th>
                  <th className="py-1 pr-3">SMA-50</th>
                </tr>
              </thead>
              <tbody>
                {symbols.map((sym) => {
                  const m = market[sym] ?? {};
                  return (
                    <tr key={sym} className="border-t border-border">
                      <td className="py-1 pr-3 font-medium">{sym}</td>
                      <td className="py-1 pr-3">{indicator(m.latest_price)}</td>
                      <td className="py-1 pr-3">{indicator(m.rsi_14)}</td>
                      <td className="py-1 pr-3">{indicator(m.sma_20)}</td>
                      <td className="py-1 pr-3">{indicator(m.sma_50)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Policy results
          {policyFromApproval ? (
            <span className="ml-2 font-normal normal-case tracking-normal text-muted">
              (from the approval re-check)
            </span>
          ) : null}
        </h4>
        {policy ? (
          <table className="mt-1 w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="py-1 pr-3">Policy</th>
                <th className="py-1 pr-3">Result</th>
                <th className="py-1">Reason &amp; evidence</th>
              </tr>
            </thead>
            <tbody>
              {policy.evaluations.map((ev) => {
                const style = outcomeStyle(ev.outcome);
                return (
                  <tr key={ev.policy_id} className="border-t border-border align-top">
                    <td className="py-1 pr-3">
                      <span className="font-medium">{ev.policy_id}</span>
                      <span className="block text-xs text-muted">{ev.policy_name}</span>
                    </td>
                    <td className="py-1 pr-3">
                      <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${style.className}`}>
                        {style.label}
                      </span>
                    </td>
                    <td className="py-1">
                      <p>{ev.reason}</p>
                      {formatEvidence(ev.evidence).map((line) => (
                        <p key={line} className="text-xs text-muted">
                          {line}
                        </p>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="mt-1 text-muted">Policies were not evaluated for this cycle.</p>
        )}
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Approval</h4>
          {cycle.approval ? (
            <p className="mt-1">
              <Badge tone={cycle.approval.status === "APPROVED" ? "positive" : "neutral"}>
                {cycle.approval.status}
              </Badge>{" "}
              {cycle.approval.acted_at ? (
                <span className="text-muted">at {formatDateTime(cycle.approval.acted_at)}</span>
              ) : null}
              {cycle.approval.comment ? (
                <span className="block text-xs text-muted">“{cycle.approval.comment}”</span>
              ) : null}
            </p>
          ) : (
            <p className="mt-1 text-muted">No approval required or recorded.</p>
          )}
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Broker outcome</h4>
          <p className="mt-1">
            <Badge tone={executionStatus === "ORDER_SUBMITTED" ? "positive" : "neutral"}>
              {executionStatus ?? "—"}
            </Badge>
          </p>
        </div>
      </section>
    </>
  );
}

function indicator(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value !== "") return value;
  return "—";
}
