"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getCurrentUser,
  getKillSwitch,
  setKillSwitch,
  triggerCycle,
  type CurrentUser,
  type KillSwitch,
} from "@/lib/agent/controls-api";
import type { AgentCycle } from "@/lib/agent/cycles-api";
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";
import { Badge } from "@/components/ui/badge";

export function SettingsView() {
  const [user, setUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-lg font-semibold">Settings</h2>
        <p className="text-sm text-muted">Agent controls and account configuration.</p>
      </section>
      <TriggerNowCard />
      <KillSwitchCard isAdmin={user?.role === "ADMIN"} />
    </div>
  );
}

function TriggerNowCard() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AgentCycle | null>(null);
  const [message, setMessage] = useState<{ tone: "neutral" | "negative"; text: string } | null>(
    null,
  );

  async function onTrigger() {
    setBusy(true);
    setMessage(null);
    try {
      const cycle = await triggerCycle();
      setResult(cycle);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setMessage({ tone: "neutral", text: "A cycle is already running — try again shortly." });
      } else {
        setMessage({ tone: "negative", text: getErrorMessage(error, "Could not trigger a cycle.") });
      }
    } finally {
      setBusy(false);
    }
  }

  const execStatus =
    result && typeof result.snapshot_json?.execution_status === "string"
      ? (result.snapshot_json.execution_status as string)
      : null;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h3 className="text-sm font-semibold">Trigger Now</h3>
      <p className="mt-1 text-sm text-muted">
        Run one on-demand agent cycle. Proposed trades appear in Approvals for review.
      </p>
      <button
        type="button"
        onClick={() => void onTrigger()}
        disabled={busy}
        className="mt-3 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
      >
        {busy ? "Running..." : "Trigger Now"}
      </button>

      {message && (
        <p
          role="status"
          className={`mt-3 text-sm ${message.tone === "negative" ? "text-negative" : "text-muted"}`}
        >
          {message.text}
        </p>
      )}
      {result && (
        <p role="status" className="mt-3 text-sm">
          Cycle <Badge tone={result.status === "FAILED" ? "negative" : "neutral"}>{result.status}</Badge>
          {result.proposal_json?.action ? (
            <span className="ml-2">
              {result.proposal_json.action} {result.proposal_json.symbol}
            </span>
          ) : null}
          {execStatus ? <span className="ml-2 text-muted">({execStatus})</span> : null}
        </p>
      )}
    </section>
  );
}

function KillSwitchCard({ isAdmin }: { isAdmin: boolean }) {
  const [state, setState] = useState<KillSwitch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingTarget, setPendingTarget] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await getKillSwitch());
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e, "Could not read the kill-switch state."));
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  async function confirmToggle() {
    if (pendingTarget === null) return;
    const target = pendingTarget;
    setBusy(true);
    setError(null);
    try {
      setState(await setKillSwitch(target));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("The kill switch is locked on by the environment and cannot be changed here.");
      } else {
        setError(getErrorMessage(e, "Could not update the kill switch."));
      }
    } finally {
      setBusy(false);
      setPendingTarget(null);
    }
  }

  if (!isAdmin) {
    return (
      <section className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold">Kill switch</h3>
        <p className="mt-1 text-sm text-muted">
          The global kill switch is available to administrators only.
        </p>
      </section>
    );
  }

  const enabled = state?.enabled ?? false;
  const envLocked = state?.enforced_by_environment ?? false;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Kill switch</h3>
        <Badge tone={enabled ? "negative" : "positive"}>{enabled ? "ENABLED" : "OFF"}</Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        Immediately blocks all broker order dispatch. Enforcement is server-side; this only toggles it.
      </p>

      {envLocked && (
        <p className="mt-2 text-sm text-muted">Locked ON by environment — cannot be changed here.</p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-negative">
          {error}
        </p>
      )}

      {pendingTarget === null ? (
        <button
          type="button"
          disabled={busy || envLocked}
          onClick={() => setPendingTarget(!enabled)}
          className="mt-3 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
        >
          {enabled ? "Disable kill switch" : "Enable kill switch"}
        </button>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="text-sm">
            {pendingTarget
              ? "Enable the kill switch and block all order dispatch?"
              : "Disable the kill switch and allow order dispatch again?"}
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => void confirmToggle()}
            className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-50"
          >
            {busy ? "Working..." : "Confirm"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setPendingTarget(null)}
            className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
