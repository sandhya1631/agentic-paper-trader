"""Cash and reserve validation (Story 3.1.3 / Issue #30).

Acceptance criteria:
  - Estimated order cost cannot exceed available cash after reserve.
  - Evidence includes all calculation values.

Design notes:
  - Fails CLOSED: if the account data cannot be fetched, the order is
    rejected (we never guess that enough cash is available).
  - Mirrors risk/market_hours.py and risk/allocation.py + risk/execution.py: a
    pure check function plus a wrapper that only calls the broker when the
    check passes.
  - The existing policy P-008 (policies.py) already computes
    `cash - equity * cash_reserve` from PolicyContext; this module supplies
    real cash/equity for that same calculation outside the policy-engine
    path, via `fetch_cash_and_equity_for()`. No new Alpaca endpoint is
    needed: AlpacaClient.get_account() already returns both fields.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CashReserveResult:
    """Outcome of the cash/reserve check."""

    allowed: bool
    reason: str
    order_cost: Decimal
    available_after_reserve: Decimal | None = None


def check_sufficient_cash(
    cash: Decimal,
    equity: Decimal,
    cash_reserve_pct: Decimal,
    order_cost: Decimal,
) -> CashReserveResult:
    """Pure check: order cost cannot exceed cash remaining after the reserve."""
    available = cash - equity * cash_reserve_pct
    if order_cost <= available:
        return CashReserveResult(True, "Sufficient cash after reserve", order_cost, available)

    return CashReserveResult(
        False,
        f"Order cost {order_cost} exceeds available cash {available} after reserve",
        order_cost,
        available,
    )


async def fetch_cash_and_equity_for(alpaca: Any) -> tuple[Decimal, Decimal]:
    """Convenience wrapper: pull real cash/equity from an existing AlpacaClient."""
    account = await alpaca.get_account()
    return account.cash, account.equity


async def validate_cash_reserve(
    fetch_account: Callable[[], Awaitable[tuple[Decimal, Decimal]]],
    cash_reserve_pct: Decimal,
    order_cost: Decimal,
) -> CashReserveResult:
    """Fetch real cash/equity and check. Any fetch error means reject (fail closed)."""
    try:
        cash, equity = await fetch_account()
    except Exception as exc:  # noqa: BLE001 - deliberately fail closed
        return CashReserveResult(
            False,
            f"Could not verify available cash, rejecting order: {exc}",
            order_cost,
        )
    return check_sufficient_cash(cash, equity, cash_reserve_pct, order_cost)


async def execute_with_cash_check(
    fetch_account: Callable[[], Awaitable[tuple[Decimal, Decimal]]],
    cash_reserve_pct: Decimal,
    order_cost: Decimal,
    submit_order: Callable[[], Awaitable[T]],
) -> tuple[CashReserveResult, T | None]:
    """Check cash/reserve before calling the broker (broker never called if insufficient)."""
    result = await validate_cash_reserve(fetch_account, cash_reserve_pct, order_cost)

    if not result.allowed:
        return result, None

    broker_result = await submit_order()
    return result, broker_result
