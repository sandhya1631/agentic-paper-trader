"""Tests for the daily-limit helpers (Story 3.2.3 / Issue #74)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from test_policies import failed_ids, make_ctx

from app.broker.schemas import AlpacaOrderRead
from app.db.enums import PolicyDecisionType
from app.risk.daily_limits import count_orders_today, daily_pnl_pct, trading_day
from app.risk.policies import evaluate_policies

# 2026-10-20 15:00 UTC = 11:00 US/Eastern (market open, EDT is UTC-4)
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def _order(submitted_at: str | None, order_id: str = "o1") -> AlpacaOrderRead:
    return AlpacaOrderRead(
        id=order_id,
        client_order_id=f"c-{order_id}",
        symbol="AAPL",
        qty=Decimal("1"),
        filled_qty=Decimal("0"),
        side="buy",
        type="market",
        time_in_force="day",
        status="accepted",
        submitted_at=submitted_at,
    )


# --- trading day (US/Eastern, not UTC) ---


def test_trading_day_uses_eastern_time_not_utc():
    # 02:00 UTC on the 21st is still 22:00 on the 20th in New York.
    assert trading_day(datetime(2026, 10, 21, 2, 0, tzinfo=UTC)) == date(2026, 10, 20)
    # 05:00 UTC on the 21st is 01:00 on the 21st in New York.
    assert trading_day(datetime(2026, 10, 21, 5, 0, tzinfo=UTC)) == date(2026, 10, 21)


def test_trading_day_requires_timezone():
    with pytest.raises(ValueError):
        trading_day(datetime(2026, 10, 20, 15, 0))


# --- orders today ---


def test_count_orders_today_counts_only_todays_orders():
    orders = [
        _order("2026-10-20T13:35:00Z", "a"),  # today 09:35 ET
        _order("2026-10-20T19:59:59.123456789Z", "b"),  # today, long fractional seconds
        _order("2026-10-21T01:30:00Z", "c"),  # 21:30 ET on the 20th: still today
        _order("2026-10-21T05:00:00Z", "d"),  # tomorrow in New York
        _order("2026-10-19T15:00:00Z", "e"),  # yesterday
    ]

    assert count_orders_today(orders, now=NOW) == 3


def test_count_orders_today_skips_orders_without_a_timestamp():
    orders = [_order(None, "a"), _order("2026-10-20T14:00:00Z", "b")]

    assert count_orders_today(orders, now=NOW) == 1


def test_count_orders_today_no_orders_is_zero():
    assert count_orders_today([], now=NOW) == 0


def test_count_orders_today_fails_closed_on_timestamp_without_timezone():
    with pytest.raises(ValueError):
        count_orders_today([_order("2026-10-20T14:00:00")], now=NOW)


def test_count_orders_today_fails_closed_on_unreadable_timestamp():
    with pytest.raises(ValueError):
        count_orders_today([_order("not-a-date")], now=NOW)


# --- daily P&L ---


def test_daily_pnl_pct_loss_and_gain():
    assert daily_pnl_pct(Decimal("970"), Decimal("1000")) == Decimal("-0.03")
    assert daily_pnl_pct(Decimal("1010"), Decimal("1000")) == Decimal("0.01")
    assert daily_pnl_pct(Decimal("1000"), Decimal("1000")) == Decimal("0")


@pytest.mark.parametrize("last_equity", [Decimal("0"), Decimal("-5")])
def test_daily_pnl_pct_fails_closed_without_valid_previous_equity(last_equity):
    with pytest.raises(ValueError):
        daily_pnl_pct(Decimal("1000"), last_equity)


# --- the helpers feed the real policies (P-011 and P-012) ---


def test_daily_loss_limit_denies_when_account_is_down_3_percent():
    pnl = daily_pnl_pct(Decimal("97000"), Decimal("100000"))  # -3%, limit is 2%

    decision = evaluate_policies(make_ctx(daily_pnl_pct=pnl))

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-011" in failed_ids(decision)


def test_daily_loss_limit_allows_a_small_loss():
    pnl = daily_pnl_pct(Decimal("99000"), Decimal("100000"))  # -1%, limit is 2%

    decision = evaluate_policies(make_ctx(daily_pnl_pct=pnl))

    assert "P-011" not in failed_ids(decision)


def test_max_orders_per_day_denies_when_the_limit_is_reached():
    orders = [_order(f"2026-10-20T14:{i:02d}:00Z", f"o{i}") for i in range(10)]
    todays = count_orders_today(orders, now=NOW)

    decision = evaluate_policies(make_ctx(orders_today=todays))

    assert todays == 10
    assert decision.decision == PolicyDecisionType.DENY
    assert "P-012" in failed_ids(decision)


def test_max_orders_per_day_allows_below_the_limit():
    orders = [_order(f"2026-10-20T14:{i:02d}:00Z", f"o{i}") for i in range(9)]

    decision = evaluate_policies(make_ctx(orders_today=count_orders_today(orders, now=NOW)))

    assert "P-012" not in failed_ids(decision)