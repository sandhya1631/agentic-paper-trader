"use client";

import { useState } from "react";
import {
  createOrder,
  type OrderSide,
  type OrderType,
  type OrderCreate,
} from "@/lib/broker/broker-api";
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";

type Feedback = { tone: "success" | "error"; message: string };

/** Map broker/manual-order errors to distinct, readable messages (423 vs 502). */
export function describeOrderError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 423) {
      return "Blocked by the global kill switch. Nothing was sent to the broker.";
    }
    if (error.status === 502) {
      return "The broker did not confirm the order. Check Positions before retrying.";
    }
  }
  return getErrorMessage(error, "Could not submit the order.");
}

/**
 * Manual order ticket (#143). This is the MANUAL OVERRIDE path: it bypasses the
 * agent's risk policies and is stopped only by the kill switch. Two-step confirm.
 */
export function ManualOrderTicket({ onSubmitted }: { onSubmitted?: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState(1);
  const [side, setSide] = useState<OrderSide>("BUY");
  const [type, setType] = useState<OrderType>("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const validSymbol = symbol.trim().length > 0;
  const validQty = Number.isInteger(qty) && qty > 0;
  const validLimit = type !== "LIMIT" || Number(limitPrice) > 0;
  const canSubmit = validSymbol && validQty && validLimit;

  async function submit() {
    setBusy(true);
    setFeedback(null);
    const order: OrderCreate = {
      symbol: symbol.trim().toUpperCase(),
      qty,
      side,
      type,
      time_in_force: "DAY",
      ...(type === "LIMIT" ? { limit_price: Number(limitPrice) } : {}),
    };
    const [res] = await Promise.allSettled([createOrder(order)]);
    if (res.status === "fulfilled") {
      setFeedback({
        tone: "success",
        message: `Manual ${side} ${qty} ${order.symbol} submitted (status: ${res.value.status}).`,
      });
      setConfirming(false);
      onSubmitted?.();
    } else {
      setFeedback({ tone: "error", message: describeOrderError(res.reason) });
    }
    setBusy(false);
  }

  return (
    <section className="space-y-3 rounded-xl border border-negative/40 bg-negative/5 p-5">
      <div>
        <h3 className="text-sm font-semibold">Manual order (override)</h3>
        <p className="mt-1 text-sm text-negative">
          Manual override — bypasses the risk policies (kill switch only). This order is{" "}
          <strong>not</strong> checked against the agent&apos;s guardrails.
        </p>
      </div>

      {feedback && (
        <p
          role={feedback.tone === "error" ? "alert" : "status"}
          className={`text-sm ${feedback.tone === "error" ? "text-negative" : "text-positive"}`}
        >
          {feedback.message}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="mo-symbol" className="text-xs font-medium text-muted">
            Symbol
          </label>
          <input
            id="mo-symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            autoComplete="off"
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm uppercase"
          />
        </div>
        <div>
          <label htmlFor="mo-qty" className="text-xs font-medium text-muted">
            Quantity (shares)
          </label>
          <input
            id="mo-qty"
            type="number"
            min={1}
            step={1}
            value={qty}
            onChange={(e) => setQty(Math.floor(Number(e.target.value)))}
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label htmlFor="mo-side" className="text-xs font-medium text-muted">
            Side
          </label>
          <select
            id="mo-side"
            value={side}
            onChange={(e) => setSide(e.target.value as OrderSide)}
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
        <div>
          <label htmlFor="mo-type" className="text-xs font-medium text-muted">
            Type
          </label>
          <select
            id="mo-type"
            value={type}
            onChange={(e) => setType(e.target.value as OrderType)}
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="MARKET">MARKET</option>
            <option value="LIMIT">LIMIT</option>
          </select>
        </div>
        {type === "LIMIT" && (
          <div>
            <label htmlFor="mo-limit" className="text-xs font-medium text-muted">
              Limit price
            </label>
            <input
              id="mo-limit"
              type="number"
              min={0}
              step="0.01"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
        )}
      </div>

      {!confirming ? (
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => {
            setFeedback(null);
            setConfirming(true);
          }}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
        >
          Review manual order
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm">
            Submit manual <strong>{side}</strong> of <strong>{qty}</strong>{" "}
            <strong>{symbol.trim().toUpperCase()}</strong> ({type})? This bypasses the risk policies.
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => void submit()}
            className="rounded-lg bg-negative px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Submitting..." : "Confirm override"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirming(false)}
            className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
