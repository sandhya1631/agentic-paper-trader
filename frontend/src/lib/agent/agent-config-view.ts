/**
 * Pure view helpers for the Settings "Agent configuration" card (#141).
 *
 * Kept free of React so they can be unit-tested in the node test environment.
 * Client-side validation is for UX only: the backend PATCH (422) and the watchlist
 * policy (P-005) remain authoritative.
 */
import { ApiError } from "@/lib/api/errors";
import { getErrorMessage } from "@/lib/api/error-message";
import { isAccountNotConnected } from "@/lib/broker/broker-api";
import { toNumber } from "@/lib/format";
import type { BarsResponse, MarketSnapshot } from "@/lib/market-data/market-data-api";
import type { AgentConfig, AgentConfigUpdate } from "./agent-config-api";

/** Mirrors the backend `Ticker` type (schemas.py) after it upper-cases the input. */
export const TICKER_PATTERN = /^[A-Z][A-Z0-9.]{0,9}$/;

export const AUTO_WARNING =
  "AUTO — no human review: trades auto-execute after the risk policies pass.";

export const NO_AGENT_MESSAGE =
  "No agent yet. Use Trigger Now below to create your default agent, then configure it here.";

export const SAVED_MESSAGE = "Saved. The next cycle uses this configuration.";

export function normalizeSymbol(raw: string): string {
  return raw.trim().toUpperCase();
}

/** Trim, upper-case, drop blanks and de-duplicate (keeping the first occurrence's order). */
export function normalizeWatchlist(list: readonly string[]): string[] {
  const out: string[] = [];
  for (const raw of list) {
    const symbol = normalizeSymbol(raw);
    if (symbol && !out.includes(symbol)) out.push(symbol);
  }
  return out;
}

/**
 * Validate a symbol about to be added. `max` is `/meta/constants` max_watchlist_size,
 * or null when unknown (the backend then decides). Returns a message, or null if valid.
 */
export function validateSymbol(
  raw: string,
  current: readonly string[],
  max: number | null,
): string | null {
  const symbol = normalizeSymbol(raw);
  if (!symbol) return "Enter a symbol.";
  if (!TICKER_PATTERN.test(symbol)) {
    return "Not a valid ticker: 1–10 letters, digits or dots, starting with a letter.";
  }
  if (normalizeWatchlist(current).includes(symbol)) {
    return `${symbol} is already in the watchlist.`;
  }
  if (max !== null && normalizeWatchlist(current).length >= max) {
    return `The watchlist is full (${max} symbols max).`;
  }
  return null;
}

/** Validate the whole draft watchlist. Returns a message, or null if it can be saved. */
export function validateWatchlist(
  list: readonly string[],
  max: number | null = null,
): string | null {
  const symbols = normalizeWatchlist(list);
  if (symbols.length === 0) return "The watchlist needs at least one symbol.";
  if (max !== null && symbols.length > max) {
    return `The watchlist can hold at most ${max} symbols.`;
  }
  const invalid = symbols.find((s) => !TICKER_PATTERN.test(s));
  if (invalid) return `${invalid} is not a valid ticker.`;
  return null;
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

/** Only the fields that differ from the saved config (an empty object means "unchanged"). */
export function buildConfigPatch(
  original: Pick<AgentConfig, "watchlist" | "approval_mode">,
  draft: { watchlist: readonly string[]; approval_mode: string },
): AgentConfigUpdate {
  const patch: AgentConfigUpdate = {};
  const watchlist = normalizeWatchlist(draft.watchlist);
  if (!sameList(watchlist, original.watchlist)) patch.watchlist = watchlist;
  if (draft.approval_mode !== original.approval_mode) patch.approval_mode = draft.approval_mode;
  return patch;
}

export function isAutoMode(mode: string): boolean {
  return mode === "AUTO";
}

const APPROVAL_MODES: Record<string, { label: string; help: string }> = {
  OBSERVE_ONLY: {
    label: "Observe only",
    help: "The agent records its proposals; nothing is executed.",
  },
  REQUIRED: {
    label: "Approval required",
    help: "Every proposed trade waits for a human decision in Approvals.",
  },
  AUTO: {
    label: "Auto",
    help: "Trades skip the Approvals queue and execute once the risk policies pass.",
  },
};

/** Label + help text for an approval mode; unknown values render raw. */
export function describeApprovalMode(mode: string): { label: string; help: string } {
  return APPROVAL_MODES[mode] ?? { label: mode, help: "" };
}

/** Select options: the `/meta/constants` modes, always including the current value. */
export function approvalModeOptions(
  modes: readonly string[] | null | undefined,
  current: string,
): string[] {
  const options = modes && modes.length > 0 ? [...modes] : [];
  if (!options.includes(current)) options.push(current);
  return options;
}

/** GET /agent/config answers 404 when the user has no agent yet (an empty state, not an error). */
export function isNoAgentError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function hasDetail(error: ApiError): boolean {
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" || (Array.isArray(detail) && detail.length > 0);
}

/** Map load/save failures to distinct, readable messages. */
export function describeAgentConfigError(error: unknown, action: "load" | "save"): string {
  const fallback =
    action === "load"
      ? "Could not load the agent configuration."
      : "Could not save the agent configuration.";
  if (error instanceof ApiError) {
    if (error.status === 404 && action === "load") return NO_AGENT_MESSAGE;
    if (error.status === 403) {
      return action === "save"
        ? "Only operators and admins can change the agent configuration."
        : "You do not have access to this agent configuration.";
    }
    // 422 and anything else: prefer the backend's detail. Without one, avoid
    // getErrorMessage's login-specific wording for 401/409.
    return hasDetail(error) ? getErrorMessage(error, fallback) : fallback;
  }
  return getErrorMessage(error, fallback);
}

/** A fraction as a plain percent with no sign, e.g. "0.10" -> "10%", "0.025" -> "2.5%". */
export function formatFraction(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 }).format(n);
}

/** What the add-symbol preview shows. Values are display strings, or null for "—". */
export interface SymbolPreview {
  symbol: string;
  latestClose: string | null;
  rsi14: string | null;
  sma20: string | null;
  sma50: string | null;
  /** Shown when no price data was found; it does not block adding. */
  warning: string | null;
  /** Extra context, e.g. why the indicators are missing. */
  note: string | null;
}

/**
 * Combine the snapshot (latest close + indicators; needs a connected account) and the
 * bars (server credentials) into one preview, degrading gracefully when either fails.
 */
export function summarizePreview(
  symbol: string,
  snapshot: PromiseSettledResult<MarketSnapshot>,
  bars: PromiseSettledResult<BarsResponse>,
): SymbolPreview {
  if (snapshot.status === "fulfilled") {
    const s = snapshot.value;
    return {
      symbol,
      latestClose: s.latest_close || null,
      rsi14: s.rsi_14 || null,
      sma20: s.sma_20 || null,
      sma50: s.sma_50 || null,
      warning: null,
      note: null,
    };
  }

  const list = bars.status === "fulfilled" ? (bars.value[symbol] ?? []) : [];
  const lastBar = list.length > 0 ? list[list.length - 1] : null;
  const notConnected = isAccountNotConnected(snapshot.reason);
  const snapshotFoundNoData =
    snapshot.reason instanceof ApiError && snapshot.reason.status === 400 && !notConnected;
  const noData = !lastBar && (bars.status === "fulfilled" || snapshotFoundNoData);

  let note: string | null = null;
  if (notConnected) {
    note = "Connect an Alpaca paper account (above) to preview RSI and SMA.";
  } else if (!noData) {
    note = lastBar
      ? "Indicators are unavailable right now."
      : `Could not load market data for ${symbol}.`;
  }

  return {
    symbol,
    latestClose: lastBar ? String(lastBar.close) : null,
    rsi14: null,
    sma20: null,
    sma50: null,
    warning: noData
      ? `No market data found for ${symbol}. You can still add it; the backend and the risk policies decide what gets traded.`
      : null,
    note,
  };
}
