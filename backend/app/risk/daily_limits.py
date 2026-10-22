"""Daily-limit helpers for the daily-loss and max-orders-per-day rules (Story 3.2.3 / Issue #74).

The rules themselves already exist as policies P-011 (daily loss) and P-012 (orders per
day) in policies.py. They only work if they are fed REAL numbers, so this module holds the
pure functions that turn broker data into those numbers.

Design notes:
  - "Today" means the US/Eastern market day (not UTC midnight), so the order count and the
    daily loss reset together with the trading day.
  - Fails CLOSED: anything we cannot trust (no previous equity, a timestamp without a
    timezone, an unreadable timestamp) raises ValueError. The caller must treat that as a
    DENY, never as "no orders today" or "no loss today".
  - Pure and database-free, like the policies.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.schemas import AlpacaOrderRead

MARKET_TZ = ZoneInfo("America/New_York")


def trading_day(moment: datetime | None = None) -> date:
    """Return the US/Eastern calendar date for ``moment`` (default: now)."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("trading_day needs a timezone-aware datetime")
    return moment.astimezone(MARKET_TZ).date()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Unreadable order timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Order timestamp has no timezone: {value!r}")
    return parsed


def count_orders_today(
    orders: Iterable[AlpacaOrderRead], now: datetime | None = None
) -> int:
    """Count orders submitted during the current US/Eastern trading day.

    Every order submitted today counts (including ones later canceled or rejected):
    the limit is about how much the agent *tried* to do. Orders with no
    ``submitted_at`` are skipped.
    """
    today = trading_day(now)
    count = 0
    for order in orders:
        if not order.submitted_at:
            continue
        if trading_day(_parse_timestamp(order.submitted_at)) == today:
            count += 1
    return count


def daily_pnl_pct(equity: Decimal, last_equity: Decimal) -> Decimal:
    """Today's profit/loss as a fraction of yesterday's closing equity.

    Example: equity 970, last_equity 1000 -> Decimal("-0.03") (down 3%).
    """
    if last_equity <= 0:
        raise ValueError("Previous equity must be positive to compute daily P&L")
    return (equity - last_equity) / last_equity