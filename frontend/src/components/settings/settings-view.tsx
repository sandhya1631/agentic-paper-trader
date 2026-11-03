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
import { listCycles } from "@/lib/agent/cycles-api";
import {
  connectAccount,
  disconnectAccount,
  getAccountStatus,
  type TradingAccount,
} from "@/lib/accounts/accounts-api";
import {
  getSchedulerStatus,
  startSchedule,
  stopSchedule,
  type SchedulerScheduleRow,
} from "@/lib/agent/scheduler-api";
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { SchedulerActivity } from "@/components/observability/scheduler-activity";

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
      <AccountCard />
      <TriggerNowCard />
      <SchedulerCard />
      <SchedulerActivity />
      <KillSwitchCard isAdmin={user?.role === "ADMIN"} />
    </div>
  );
}

function AccountCard() {
  const [account, setAccount] = useState<TradingAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setAccount(await getAccountStatus());
    } catch (e) {
      // 404 just means "nothing connected yet" — show the form, not an error.
      if (!(e instanceof ApiError && e.status === 404)) {
        setError(getErrorMessage(e, "Could not read account status."));
      }
      setAccount(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onConnect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await connectAccount({ api_key: apiKey.trim(), api_secret: apiSecret.trim(), is_paper: true });
      setApiKey("");
      setApiSecret("");
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not connect the account."));
    } finally {
      setBusy(false);
    }
  }

  async function onDisconnect() {
    setBusy(true);
    setError(null);
    try {
      await disconnectAccount();
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not disconnect the account."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h3 className="text-sm font-semibold">Alpaca paper account</h3>
      {error && (
        <p role="alert" className="mt-2 text-sm text-negative">
          {error}
        </p>
      )}
      {loading ? (
        <p className="mt-1 text-sm text-muted">Loading account status...</p>
      ) : account?.is_connected ? (
        <div className="mt-2 space-y-2 text-sm">
          <p>
            <Badge tone="positive">Connected</Badge>{" "}
            <span className="text-muted">
              {account.provider} · {account.is_paper ? "paper" : "live"}
            </span>
          </p>
          <button
            type="button"
            onClick={() => void onDisconnect()}
            disabled={busy}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
          >
            {busy ? "Working..." : "Disconnect"}
          </button>
        </div>
      ) : (
        <form onSubmit={onConnect} className="mt-2 space-y-3">
          <p className="text-sm text-muted">
            Connect your Alpaca paper-trading API keys. Keys are encrypted at rest and never shown
            again.
          </p>
          <div>
            <label htmlFor="alpaca-key" className="text-xs font-medium text-muted">
              API key
            </label>
            <input
              id="alpaca-key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              required
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="alpaca-secret" className="text-xs font-medium text-muted">
              API secret
            </label>
            <input
              id="alpaca-secret"
              type="password"
              value={apiSecret}
              onChange={(e) => setApiSecret(e.target.value)}
              autoComplete="off"
              required
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !apiKey.trim() || !apiSecret.trim()}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
          >
            {busy ? "Connecting..." : "Connect account"}
          </button>
        </form>
      )}
    </section>
  );
}

function SchedulerCard() {
  const [schedule, setSchedule] = useState<SchedulerScheduleRow | null>(null);
  const [running, setRunning] = useState(false);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [interval, setIntervalMinutes] = useState(15);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const status = await getSchedulerStatus();
      setRunning(status.running);
      setSchedule(status.schedules[0] ?? null);
      if (status.schedules[0]) {
        setAgentId(status.schedules[0].agent_id);
      } else {
        // Derive the user's agent id from their latest cycle, if any.
        const cycles = await listCycles({ limit: 1 });
        setAgentId(cycles.items[0]?.agent_id ?? null);
      }
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e, "Could not read scheduler status."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onStart() {
    if (!agentId) return;
    setBusy(true);
    setError(null);
    try {
      await startSchedule(agentId, interval);
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not start the schedule."));
    } finally {
      setBusy(false);
    }
  }

  async function onStop() {
    if (!schedule) return;
    setBusy(true);
    setError(null);
    try {
      await stopSchedule(schedule.id);
      await load();
    } catch (e) {
      setError(getErrorMessage(e, "Could not stop the schedule."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Autonomous schedule</h3>
        <Badge tone={schedule?.enabled && running ? "positive" : "neutral"}>
          {schedule?.enabled && running ? "Running" : "Stopped"}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        Run the agent automatically on an interval. Scheduled ticks are skipped while the market is
        closed.
      </p>

      {error && (
        <p role="alert" className="mt-2 text-sm text-negative">
          {error}
        </p>
      )}

      {loading ? (
        <p className="mt-2 text-sm text-muted">Loading schedule...</p>
      ) : schedule ? (
        <div className="mt-2 space-y-2 text-sm">
          <p className="text-muted">
            Every {schedule.interval_minutes} min · next run {formatDateTime(schedule.next_run_time)}
          </p>
          <button
            type="button"
            onClick={() => void onStop()}
            disabled={busy}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
          >
            {busy ? "Working..." : "Stop schedule"}
          </button>
        </div>
      ) : agentId ? (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          <label htmlFor="sched-interval" className="text-muted">
            Interval (min)
          </label>
          <input
            id="sched-interval"
            type="number"
            min={1}
            max={1440}
            value={interval}
            onChange={(e) => setIntervalMinutes(Number(e.target.value))}
            className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-sm"
          />
          <button
            type="button"
            onClick={() => void onStart()}
            disabled={busy || interval < 1}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
          >
            {busy ? "Starting..." : "Start schedule"}
          </button>
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted">
          Trigger a cycle first (above) to create your agent, then you can schedule it.
        </p>
      )}
    </section>
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
  const [reason, setReason] = useState("");

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
      setState(await setKillSwitch(target, reason));
      setReason("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("The kill switch is locked on by the environment and cannot be changed here.");
      } else if (e instanceof ApiError && e.status === 422) {
        setError("A reason is required to enable the kill switch.");
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
  // Turning the switch ON must come with a reason; turning it off does not need one.
  const needsReason = pendingTarget === true;
  const reasonMissing = needsReason && reason.trim().length === 0;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Kill switch</h3>
        <Badge tone={enabled ? "negative" : "positive"}>{enabled ? "ENABLED" : "OFF"}</Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        Immediately blocks all broker order dispatch. Enforcement is server-side; this only toggles it.
      </p>

      {enabled && state?.reason ? (
        <p className="mt-2 text-sm">
          <span className="text-muted">Reason: </span>
          {state.reason}
        </p>
      ) : null}
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
          onClick={() => {
            setReason("");
            setPendingTarget(!enabled);
          }}
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
          {needsReason && (
            <div className="w-full">
              <label htmlFor="kill-switch-reason" className="text-xs font-medium text-muted">
                Reason (required)
              </label>
              <input
                id="kill-switch-reason"
                type="text"
                value={reason}
                maxLength={500}
                onChange={(event) => setReason(event.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </div>
          )}
          <button
            type="button"
            disabled={busy || reasonMissing}
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