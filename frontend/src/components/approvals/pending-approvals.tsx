"use client";

import { useCallback, useEffect, useState } from "react";
import { getErrorMessage } from "@/lib/api/error-message";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { usePolling } from "@/lib/hooks/use-polling";
import {
  approveApproval,
  listPendingApprovals,
  rejectApproval,
  type PendingApproval,
} from "@/lib/approvals/approvals-api";
import {
  REJECT_MESSAGE,
  describeApprovalError,
  describeCycleOutcome,
  formatCountdown,
  formatEvidence,
  isExpired,
  outcomeStyle,
  parsePolicyResult,
  type Feedback,
} from "@/lib/approvals/approval-view";

const feedbackStyles: Record<Feedback["tone"], string> = {
  success: "border-positive/30 bg-positive/10 text-positive",
  warning: "border-border bg-surface-muted text-foreground",
  error: "border-negative/30 bg-negative/10 text-negative",
};

/** Pending-approvals review screen: approve or reject with policy evidence (#120). */
export function PendingApprovals() {
  const [items, setItems] = useState<PendingApproval[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [now, setNow] = useState(() => Date.now());
  const [live, setLive] = useState(true);

  const load = useCallback(async () => {
    try {
      setItems(await listPendingApprovals());
      setLoadError(null);
    } catch (error) {
      setLoadError(getErrorMessage(error, "Could not load pending approvals."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Tick every second so the expiry countdowns stay live.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Auto-refresh the pending list so new proposals appear without a reload.
  usePolling(() => void load(), 15000, live);

  async function act(kind: "approve" | "reject", id: string) {
    setBusyId(id);
    setFeedback(null);
    try {
      if (kind === "approve") {
        setFeedback(describeCycleOutcome(await approveApproval(id, comments[id])));
      } else {
        await rejectApproval(id, comments[id]);
        setFeedback(REJECT_MESSAGE);
      }
    } catch (error) {
      setFeedback({ tone: "error", message: describeApprovalError(error) });
    } finally {
      setBusyId(null);
      await load();
    }
  }

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Pending approvals</h2>
          <p className="text-sm text-muted">
            Review each proposed trade with its policy results. Approving re-checks every
            policy before any paper order is sent.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AutoRefreshToggle enabled={live} onChange={setLive} seconds={15} />
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
          >
            Refresh
          </button>
        </div>
      </section>

      {feedback && (
        <div
          role="status"
          aria-live="polite"
          className={`rounded-lg border px-4 py-3 text-sm ${feedbackStyles[feedback.tone]}`}
        >
          {feedback.message}
        </div>
      )}

      {loadError && (
        <div
          role="alert"
          className="rounded-lg border border-negative/30 bg-negative/10 px-4 py-3 text-sm text-negative"
        >
          {loadError}
        </div>
      )}

      {items === null && !loadError && <p className="text-sm text-muted">Loading...</p>}

      {items !== null && items.length === 0 && (
        <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
          No proposals are waiting for approval.
        </div>
      )}

      {items?.map(({ approval, proposal }) => {
        const expired = isExpired(approval.expires_at, now);
        const policy = parsePolicyResult(approval.policy_result_json);
        const busy = busyId === approval.id;
        const commentId = `comment-${approval.id}`;

        return (
          <article
            key={approval.id}
            className="space-y-4 rounded-xl border border-border bg-surface p-5"
          >
            <header className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="text-xl font-semibold">{proposal.symbol}</span>
                <span
                  className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                    proposal.action === "BUY"
                      ? "bg-positive/10 text-positive"
                      : "bg-negative/10 text-negative"
                  }`}
                >
                  {proposal.action}
                </span>
                <span className="text-sm text-muted">{proposal.quantity} shares</span>
              </div>
              <span
                className={`rounded-md px-2 py-1 text-xs font-medium ${
                  expired ? "bg-negative/10 text-negative" : "bg-surface-muted text-muted"
                }`}
              >
                {expired ? "Expired" : `Expires in ${formatCountdown(approval.expires_at, now)}`}
              </span>
            </header>

            <p className="text-sm">{proposal.reasoning}</p>

            <details className="rounded-lg border border-border">
              <summary className="cursor-pointer px-4 py-2 text-sm font-medium">
                Policy results
                {policy ? ` (${policy.decision})` : ""}
              </summary>
              <div className="border-t border-border p-4">
                {policy ? (
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs uppercase tracking-wide text-muted">
                      <tr>
                        <th className="pb-2 pr-3">Policy</th>
                        <th className="pb-2 pr-3">Result</th>
                        <th className="pb-2">Reason and evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {policy.evaluations.map((item) => {
                        const style = outcomeStyle(item.outcome);
                        return (
                          <tr key={item.policy_id} className="border-t border-border align-top">
                            <td className="py-2 pr-3">
                              <span className="font-medium">{item.policy_id}</span>
                              <span className="block text-xs text-muted">{item.policy_name}</span>
                            </td>
                            <td className="py-2 pr-3">
                              <span
                                className={`rounded-md px-2 py-0.5 text-xs font-semibold ${style.className}`}
                              >
                                {style.label}
                              </span>
                            </td>
                            <td className="py-2">
                              <p>{item.reason}</p>
                              {formatEvidence(item.evidence).map((line) => (
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
                  <p className="text-sm text-muted">
                    Policy results are not available for this proposal. Every policy is checked
                    again when you approve.
                  </p>
                )}
              </div>
            </details>

            <div>
              <label htmlFor={commentId} className="text-xs font-medium text-muted">
                Comment (optional)
              </label>
              <textarea
                id={commentId}
                value={comments[approval.id] ?? ""}
                maxLength={500}
                rows={2}
                onChange={(event) =>
                  setComments((prev) => ({ ...prev, [approval.id]: event.target.value }))
                }
                className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || expired}
                onClick={() => void act("approve", approval.id)}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
              >
                {busy ? "Working..." : "Approve and place paper order"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void act("reject", approval.id)}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
              >
                Reject
              </button>
              {expired && (
                <span className="text-sm text-negative">
                  This approval has expired and can no longer be approved.
                </span>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}