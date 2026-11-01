"""Consolidated illegal-order test suite (Story 3.3.3 / Issue #78).

For each of the seven illegal-order categories in the AC - allocation, cash,
market hours, stale data, duplicates, daily limits, kill switch - this drives
the REAL production path: app.agent.approval.evaluate_and_submit_cycle_trade,
which (unlike test_cycle_approval.py / test_daily_limits_wiring.py) is allowed
to call the REAL app.broker.gateway.submit_approved_order here instead of
having it mocked away. The assertion of "zero broker calls" is made at the
RAW boundary - client.submit_order, the one call site in gateway.py that
would reach Alpaca - not at the higher submit_approved_order wrapper level.

A positive control (test_a_legal_order_does_reach_the_broker) proves the
harness isn't vacuously green: a clean proposal really does reach
client.submit_order.
"""
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_cycle_approval import _open_order, _trade_fixtures
from test_policies import failed_ids

from app.agent import approval
from app.broker import gateway as gateway_module
from app.core.config import Settings
from app.db.enums import PolicyDecisionType


def _real_gateway_fixtures(
    monkeypatch, *, market_open: bool = True, kill_switch_at_gateway: bool = False
):
    """Like test_cycle_approval._trade_fixtures, but leaves the REAL
    submit_approved_order in place (never monkeypatched away) and gives it a
    db double capable enough to survive its own query + add/commit/refresh
    calls, plus a client.submit_order AsyncMock standing in for the one real
    network call the gateway would otherwise make.
    """
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=market_open)
    account = db.execute.return_value.scalar_one_or_none()

    # The real gateway calls db.execute a second time (get_order_by_client_id,
    # "has this client_order_id been submitted before?") - always "no" here,
    # a fresh cycle the first time through.
    results = iter([
        SimpleNamespace(scalar_one_or_none=lambda: account),  # TradingAccount lookup
        SimpleNamespace(scalar_one_or_none=lambda: None),      # get_order_by_client_id -> none yet
    ])

    async def execute(*_args, **_kwargs):
        try:
            return next(results)
        except StopIteration:
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    db.execute = execute
    db.add = lambda _row: None
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    client.submit_order = AsyncMock(
        return_value=SimpleNamespace(
            id="broker-order-id",
            filled_qty=Decimal("0"),
            average_fill_price=None,
            status="accepted",
        )
    )

    # The gateway's OWN kill-switch guard imports is_kill_switch_enabled
    # independently of approval.py's copy - patch it separately so the
    # kill-switch category can prove this second guard too.
    monkeypatch.setattr(
        gateway_module, "is_kill_switch_enabled", AsyncMock(return_value=kill_switch_at_gateway)
    )
    return db, agent, cycle, client


@pytest.mark.asyncio
async def test_a_legal_order_does_reach_the_broker(monkeypatch):
    """Positive control: a clean proposal really does call client.submit_order."""
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.ALLOW
    assert order is not None
    client.submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_over_allocation_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    cycle.proposal_json["quantity"] = 2000  # far past 10% of equity

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-007" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_insufficient_cash_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    client.get_account = AsyncMock(
        return_value=SimpleNamespace(
            equity=Decimal("100000"), cash=Decimal("1"), last_equity=Decimal("100000")
        )
    )

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-008" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_closed_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch, market_open=False)

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-003" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_market_data_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    stale_timestamp = "2020-01-01T00:00:00+00:00"
    client.get_market_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            latest_close=Decimal("100"), timestamp=stale_timestamp,
            rsi_14=Decimal("35"), sma_20=Decimal("99"),
        )
    )

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-004" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_open_order_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    client.get_orders = AsyncMock(return_value=[_open_order("AAPL")])

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-010" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_order_limit_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    monkeypatch.setattr(
        approval, "get_settings", lambda: Settings(_env_file=None, max_orders_per_day=1)
    )
    filled_today = SimpleNamespace(
        symbol="MSFT", status="filled", submitted_at=datetime.now(UTC).isoformat(), id="order-1",
    )
    client.get_orders = AsyncMock(return_value=[filled_today])

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-012" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_the_broker_call(monkeypatch):
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    client.get_account = AsyncMock(
        return_value=SimpleNamespace(
            equity=Decimal("97000"), cash=Decimal("50000"), last_equity=Decimal("100000")
        )
    )

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-011" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_at_policy_layer_blocks_the_broker_call(monkeypatch):
    """P-002: the policy-level kill switch check (approval.py's copy)."""
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch)
    monkeypatch.setattr(approval, "is_kill_switch_enabled", AsyncMock(return_value=True))

    decision, order = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == PolicyDecisionType.DENY
    assert "P-002" in failed_ids(decision)
    assert order is None
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_at_gateway_layer_blocks_the_broker_call(monkeypatch):
    """Defense in depth: even if P-002 somehow passed, the gateway's OWN
    independent kill-switch check (gateway.py's copy) still blocks it before
    client.submit_order is ever reached."""
    db, agent, cycle, client = _real_gateway_fixtures(monkeypatch, kill_switch_at_gateway=True)

    from app.broker.gateway import TradingDisabledError

    with pytest.raises(TradingDisabledError):
        await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    client.submit_order.assert_not_awaited()
