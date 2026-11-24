"""Fixed, non-env-tunable domain constants shared across unrelated modules (Issue #94).

A behavior-preserving home for values that were previously copy-pasted into several
files: same values, one place to find and reuse them.

This is a leaf module: it depends only on app.db.enums (pure enum definitions with
no further app.* imports) and the standard library. Nothing else in app.* may be
imported here, and Settings (app/core/config.py) may import FROM this module but
never the other way around - that keeps the dependency direction one-way and avoids
a circular import.

Env-overridable/tunable values (retry counts, bar limits, approval expiry, ...) live
in Settings instead - see app/core/config.py.
"""

from __future__ import annotations

from app.db.enums import CycleStatus, UserRole

# --- Alpaca paper-trading endpoint ---
# The ONLY trading endpoint this app will ever submit real orders to
# (enforced at both config load and client construction - see paper_guard.py).
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_DATA_FEED = "iex"
# GET /v2/orders returns at most this many orders per request (Alpaca's maximum `limit`).
ALPACA_ORDERS_PAGE_LIMIT = 500
# Paging stops (and the read fails closed) after this many full pages, i.e. 10,000 orders.
ALPACA_ORDERS_MAX_PAGES = 20

# --- Market data ---
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "SPY"]
# Upper bound on a user-edited watchlist (PATCH /agent/config, #141). Each symbol costs
# one LLM call per scheduled tick plus a bars request; exposed via /meta/constants.
MAX_WATCHLIST_SIZE = 20
RATE_LIMIT_STATUS = 429

# --- Agent cycles ---
# Non-terminal statuses: a cycle in any of these is still "in progress" and
# blocks a new cycle from starting for the same agent (Story 2.3.2 / Issue #68).
ACTIVE_CYCLE_STATUSES = (
    CycleStatus.PENDING,
    CycleStatus.COLLECTING,
    CycleStatus.REASONING,
    CycleStatus.VALIDATING,
    CycleStatus.PENDING_APPROVAL,
    CycleStatus.EXECUTING,
    CycleStatus.RECONCILING,
)
# In-flight statuses that only a process holding the agent's cycle lock ever writes
# (the orchestrator, or an approval's execution). A cycle left in one of them while
# that lock is free was orphaned by a process that died, e.g. a redeploy (#185), or
# its run lost the lock's connection; that run stops at its next phase check.
# PENDING_APPROVAL (lazy expiry) and RECONCILING (broker reconcile) are excluded.
INTERRUPTIBLE_CYCLE_STATUSES = (
    CycleStatus.PENDING,
    CycleStatus.COLLECTING,
    CycleStatus.REASONING,
    CycleStatus.VALIDATING,
)
ORPHANABLE_CYCLE_STATUSES = (*INTERRUPTIBLE_CYCLE_STATUSES, CycleStatus.EXECUTING)
# failure_code of a cycle that recovery closed out because its process died (#185).
CYCLE_INTERRUPTED = "INTERRUPTED"
# Length of cycle_approvals.comment: the approve/reject body's limit, and the cap for the
# "no order was placed" note reconciliation appends to an approval (#210).
APPROVAL_COMMENT_MAX_LENGTH = 500
DEFAULT_STRATEGY_VERSION = "S-001"
DYNAMIC_STRATEGY_VERSION = "DYNAMIC"
CUSTOM_STRATEGY_VERSION = "CUSTOM"
# Bump whenever the S-001 prompt text changes, so each cycle's audit record names the
# exact prompt it ran. 1.1.0 (#153): per-symbol `decision_target`, no hardcoded tickers.
# 1.2.0 (#64/#172): live account data section; HOLD when the account is UNAVAILABLE.
# 1.3.0 (#64/#172 reconciliation): adds the data-quality/regime evidence rules from #64
# on top of 1.2.0's account-data HOLD rule, so the combined text gets its own revision.
DEFAULT_PROMPT_REVISION = "1.3.0"

# --- Broker credential vault ---
ALPACA_PROVIDER = "ALPACA"

# --- Risk policies ---
POLICY_VERSION = "POLICY-1.0"

# --- Audit events (audit_events.event_type) ---
# Manual override orders through POST /api/v1/broker/orders (#162): one row per attempt.
MANUAL_ORDER_SUBMITTED = "MANUAL_ORDER_SUBMITTED"
MANUAL_ORDER_BLOCKED = "MANUAL_ORDER_BLOCKED"  # the kill switch refused it (423)
MANUAL_ORDER_FAILED = "MANUAL_ORDER_FAILED"  # the broker call raised (502)
# A user connected or revoked their own AI provider key (#191); details name the provider.
LLM_KEY_CONNECTED = "LLM_KEY_CONNECTED"
LLM_KEY_DISCONNECTED = "LLM_KEY_DISCONNECTED"

# --- LLM client factory ---
SUPPORTED_PROVIDERS = ("openai", "ollama", "anthropic")
# Providers a user may connect their own API key for (#191): the hosted ones. Ollama is
# self-hosted, so it has no user key. POST /accounts/llm accepts exactly these, and
# /meta/constants exposes them for the Settings key card's provider select.
LLM_KEY_PROVIDERS = ("openai", "anthropic")
# Whose key a cycle or replay used, recorded in the cycle snapshot (#191).
LLM_KEY_SOURCE_USER = "USER"
LLM_KEY_SOURCE_SERVER = "SERVER"
# Ollama request timeout and model keep-alive (#203): the server defaults are Settings
# (OLLAMA_TIMEOUT_SECONDS, OLLAMA_KEEP_ALIVE_MINUTES); an agent may override them within
# these inclusive bounds. Keep-alive -1 keeps the model loaded; 0 unloads it after each
# call. Exposed via /meta/constants so the Settings form validates the same range.
OLLAMA_TIMEOUT_SECONDS_RANGE = (30, 900)
OLLAMA_KEEP_ALIVE_MINUTES_RANGE = (-1, 240)
# Code defaults of those Settings (the env can override them).
OLLAMA_TIMEOUT_SECONDS_DEFAULT = 180
OLLAMA_KEEP_ALIVE_MINUTES_DEFAULT = 10
# failure_code of a cycle whose LLM was misconfigured or unreachable (#160).
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"

# --- Auth ---
BCRYPT_MAX_PASSWORD_BYTES = 72
# Roles allowed to act on trading: run or schedule cycles, approve or reject proposals,
# edit the agent config (`require_operator`). Scheduled ticks skip owners outside it (#176).
OPERATOR_ROLES = (UserRole.OPERATOR, UserRole.ADMIN)
# How long a /auth/forgot-password token stays valid (Issue #166).
PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = 30

# --- Display ---
# Alpaca paper accounts are USD-only; exposed via /meta/constants (Issue #139)
# so the frontend never hardcodes this.
CURRENCY = "USD"