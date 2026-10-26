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

from app.db.enums import CycleStatus

# --- Alpaca paper-trading endpoint ---
# The ONLY trading endpoint this app will ever submit real orders to
# (enforced at both config load and client construction - see paper_guard.py).
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_DATA_FEED = "iex"

# --- Market data ---
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "SPY"]
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
DEFAULT_STRATEGY_VERSION = "S-001"
DEFAULT_PROMPT_REVISION = "1.0.0"

# --- Broker credential vault ---
ALPACA_PROVIDER = "ALPACA"

# --- Risk policies ---
POLICY_VERSION = "POLICY-1.0"

# --- LLM client factory ---
SUPPORTED_PROVIDERS = ("openai", "ollama")

# --- Auth ---
BCRYPT_MAX_PASSWORD_BYTES = 72

