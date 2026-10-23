"""Daily loss and order limits, wired into the real approval path (Story 3.2.3 / Issue #74)."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from test_cycle_approval import _trade_fixtures
from test_policies import failed_ids

from app.agent import approval
from app.core.config import Settings
from app.db.enums import PolicyDecisionType


def _order_today(order_id: str) -> SimpleNamespace:
    """A finished order for another symbol, submitted just now (so it counts for today)."""
    return SimpleNamespace(
        symbol="MSFT",
        status="filled",
        submitted_at=datetime.now(UTC).isoformat(),
        id=order_id,
    )


def _account(equity: str, last_equity: str) -> SimpleNamespace:
    return SimpleNamespace(
        equity=Decimal(equity), cash=Decimal("50000"), last_equity=Decimal(last_equity)
    )


# --- daily loss limit ---


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_the_order_before_the_broker(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_account = AsyncMock(return_value=_account("97000", "100000"))  # down 3%
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-011" in failed_ids(decision)
    assert order is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_small_loss_is_still_allowed(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_account = AsyncMock(return_value=_account("99000", "100000"))  # down 1%
    monkeypatch.setattr(
        approval, "submit_approved_order", AsyncMock(return_value=SimpleNamespace())
    )

    decision, _ = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.ALLOW


# --- max orders per day (now configurable) ---


@pytest.mark.asyncio
async def test_max_orders_per_day_comes_from_settings(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    monkeypatch.setattr(
        approval, "get_settings", lambda: Settings(_env_file=None, max_orders_per_day=2)
    )
    client.get_orders = AsyncMock(return_value=[_order_today("a"), _order_today("b")])
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-012" in failed_ids(decision)
    assert order is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_below_the_order_limit_is_allowed(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    monkeypatch.setattr(
        approval, "get_settings", lambda: Settings(_env_file=None, max_orders_per_day=2)
    )
    client.get_orders = AsyncMock(return_value=[_order_today("a")])
    monkeypatch.setattr(
        approval, "submit_approved_order", AsyncMock(return_value=SimpleNamespace())
    )

    decision, _ = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.ALLOW


def test_max_orders_per_day_defaults_to_10_and_must_be_positive():
    assert Settings(_env_file=None).max_orders_per_day == 10
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_orders_per_day=0)


# --- fail closed when the account data is missing or unreadable ---


@pytest.mark.asyncio
async def test_account_unavailable_blocks_the_order(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_account = AsyncMock(side_effect=httpx.ConnectError("alpaca is down"))
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    with pytest.raises(httpx.ConnectError):
        await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_previous_equity_blocks_the_order(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_account = AsyncMock(return_value=_account("100000", "0"))
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    with pytest.raises(ValueError):
        await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_timestamp_without_timezone_blocks_the_order(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    bad = SimpleNamespace(symbol="MSFT", status="filled", submitted_at="2026-10-20T14:00:00")
    client.get_orders = AsyncMock(return_value=[bad])
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    with pytest.raises(ValueError):
        await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    submit_order.assert_not_awaited()