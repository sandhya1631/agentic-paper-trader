"""Basic deterministic risk policies (P-001 ... P-012).

HOW TO ADD A POLICY (3 steps):
  1. Write a function `p0xx_name(ctx: PolicyContext) -> PolicyEvaluation` below.
  2. Add it to the POLICIES list at the bottom.
  3. Add a pass test and a fail test in tests/test_policies.py.

Rules every policy must follow:
  - Pure function: no DB, no network, no LLM. Everything it needs is in `ctx`.
  - Return PASS, FAIL or NOT_APPLICABLE (e.g. a SELL-only rule on a BUY).
  - Put the numbers you compared in `evidence` so the UI/audit can show them.
  - Any FAIL means DENY and the broker must never be called.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import get_settings
from app.db.enums import DecisionAction, PolicyDecisionType, PolicyOutcome
from app.risk.allocation import check_maximum_allocation
from app.schemas import PolicyDecision, PolicyEvaluation, PolicyInput

POLICY_VERSION = "POLICY-1.0"


@dataclass
class PolicyContext:
    """Everything the policies need. Build it from PolicyInput + Alpaca account state."""

    inp: PolicyInput
    equity: Decimal
    cash: Decimal
    position_qty: int = 0                      # shares already owned of the proposed symbol
    open_order_symbols: set[str] = field(default_factory=set)
    market_open: bool = True
    data_age_seconds: float = 0.0
    daily_pnl_pct: Decimal = Decimal("0")      # e.g. -0.03 means down 3% today
    is_paper: bool = True
    watchlist: set[str] = field(default_factory=lambda: {"AAPL", "NVDA", "SPY"})
    max_data_age_seconds: float = 600.0        # 10 minutes
    max_orders_per_day: int = 10

    @property
    def action(self) -> DecisionAction:
        return self.inp.proposal.action

    @property
    def symbol(self) -> str:
        return str(self.inp.proposal.symbol)

    @property
    def order_value(self) -> Decimal:
        return self.inp.market.current_price * self.inp.proposal.quantity


def _eval(pid: str, name: str, ok: bool, reason: str, **evidence) -> PolicyEvaluation:
    return PolicyEvaluation(
        policy_id=pid,
        policy_name=name,
        outcome=PolicyOutcome.PASS if ok else PolicyOutcome.FAIL,
        reason="OK" if ok else reason,
        evidence=evidence,
    )


def _na(pid: str, name: str) -> PolicyEvaluation:
    return PolicyEvaluation(
        policy_id=pid, policy_name=name, outcome=PolicyOutcome.NOT_APPLICABLE,
        reason="Not applicable to this action", evidence={},
    )


def p001_paper_only(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-001", "Paper-only broker", c.is_paper,
                 "Broker endpoint is not marked paper", is_paper=c.is_paper)


def p002_kill_switch(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-002", "Kill switch", not c.inp.kill_switch_enabled,
                 "Kill switch is ON", kill_switch=c.inp.kill_switch_enabled)


def p003_market_open(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-003", "Market open", c.market_open,
                 "Market is closed", market_open=c.market_open)


def p004_fresh_data(c: PolicyContext) -> PolicyEvaluation:
    ok = c.data_age_seconds <= c.max_data_age_seconds
    return _eval("P-004", "Fresh data", ok, "Market data is stale",
                 age_seconds=c.data_age_seconds, max_seconds=c.max_data_age_seconds)


def p005_allowlist(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-005", "Symbol allowlist", c.symbol in c.watchlist,
                 f"{c.symbol} is not in the watchlist",
                 symbol=c.symbol, watchlist=sorted(c.watchlist))


def p007_max_allocation(c: PolicyContext) -> PolicyEvaluation:
    if c.action != DecisionAction.BUY:
        return _na("P-007", "Max symbol allocation")
    current_price = c.inp.market.current_price
    result = check_maximum_allocation(
        portfolio_equity=c.equity,
        current_exposure=current_price * c.position_qty,
        proposed_order_value=c.order_value,
        max_symbol_allocation=c.inp.agent.max_symbol_allocation,
    )
    return _eval("P-007", "Max symbol allocation", result.allowed, result.reason,
                 projected=str(result.projected_exposure), limit=str(result.maximum_allowed))


def p008_available_cash(c: PolicyContext) -> PolicyEvaluation:
    if c.action != DecisionAction.BUY:
        return _na("P-008", "Available cash")
    available = c.cash - c.equity * c.inp.agent.cash_reserve
    return _eval("P-008", "Available cash", c.order_value <= available,
                 "Order cost exceeds cash after reserve",
                 cost=str(c.order_value), available_after_reserve=str(available))


def p009_sell_ownership(c: PolicyContext) -> PolicyEvaluation:
    if c.action != DecisionAction.SELL:
        return _na("P-009", "Sell ownership (no shorting)")
    qty = c.inp.proposal.quantity
    return _eval("P-009", "Sell ownership (no shorting)", qty <= c.position_qty,
                 "Cannot sell more shares than owned", quantity=qty, owned=c.position_qty)


def p010_open_order_conflict(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-010", "Open-order conflict", c.symbol not in c.open_order_symbols,
                 f"An open order already exists for {c.symbol}", symbol=c.symbol)


def p011_daily_loss(c: PolicyContext) -> PolicyEvaluation:
    limit = c.inp.agent.daily_loss_limit
    return _eval("P-011", "Daily loss limit", c.daily_pnl_pct > -limit,
                 "Daily loss limit reached", daily_pnl_pct=str(c.daily_pnl_pct),
                 limit=str(-limit))


def p012_max_orders_per_day(c: PolicyContext) -> PolicyEvaluation:
    return _eval("P-012", "Max orders per day", c.inp.orders_today < c.max_orders_per_day,
                 "Daily order limit reached",
                 orders_today=c.inp.orders_today, max=c.max_orders_per_day)


POLICIES = [
    p001_paper_only,
    p002_kill_switch,
    p003_market_open,
    p004_fresh_data,
    p005_allowlist,
    p007_max_allocation,
    p008_available_cash,
    p009_sell_ownership,
    p010_open_order_conflict,
    p011_daily_loss,
    p012_max_orders_per_day,
]


def _policy_token(ctx: PolicyContext, evaluated_at: datetime) -> str:
    """Signed proof that THIS proposal passed policies. The broker gateway should verify it."""
    p = ctx.inp.proposal
    msg = f"{POLICY_VERSION}|{p.symbol}|{p.action}|{p.quantity}|{evaluated_at.isoformat()}"
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def evaluate_policies(ctx: PolicyContext) -> PolicyDecision:
    """Run EVERY policy (no early exit, so the audit shows all evidence)."""
    evaluations = [policy(ctx) for policy in POLICIES]
    denied = any(e.outcome == PolicyOutcome.FAIL for e in evaluations)
    now = datetime.now(UTC)
    return PolicyDecision(
        decision=PolicyDecisionType.DENY if denied else PolicyDecisionType.ALLOW,
        policy_version=POLICY_VERSION,
        evaluated_at=now,
        policy_token=None if denied else _policy_token(ctx, now),
        evaluations=evaluations,
        final_quantity=None if denied else ctx.inp.proposal.quantity,
    )
