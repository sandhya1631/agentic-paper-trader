from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent import approval
from app.broker.gateway import submit_approved_order
from app.db.enums import (
    ApprovalMode,
    BrokerOrderStatus,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
)
from app.risk.market_hours import MarketClock
from app.schemas import ApprovedOrder


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_):
        return None


def _trade_fixtures(monkeypatch, *, market_open: bool):
    account = SimpleNamespace(id=uuid4(), user_id=uuid4(), is_connected=True, is_paper=True)
    agent = SimpleNamespace(
        id=uuid4(),
        account_id=account.id,
        name="Test",
        watchlist=["AAPL"],
        strategy_version="S-001",
        cadence_minutes=15,
        target_allocation=Decimal("0.05"),
        max_symbol_allocation=Decimal("0.10"),
        cash_reserve=Decimal("0.05"),
        daily_loss_limit=Decimal("0.02"),
        approval_mode=ApprovalMode.REQUIRED,
        enabled=True,
    )
    cycle = SimpleNamespace(
        id=uuid4(),
        proposal_json={
            "symbol": "AAPL",
            "action": "BUY",
            "quantity": 1,
            "reasoning": "test",
        },
    )
    client = SimpleNamespace(
        get_account=AsyncMock(
            return_value=SimpleNamespace(
                equity=Decimal("100000"),
                cash=Decimal("50000"),
                last_equity=Decimal("100000"),
            )
        ),
        get_market_snapshot=AsyncMock(
            return_value=SimpleNamespace(
                latest_close=Decimal("100"),
                timestamp=datetime.now(UTC).isoformat(),
                rsi_14=Decimal("35"),
                sma_20=Decimal("99"),
            )
        ),
        get_positions=AsyncMock(return_value=[]),
        get_orders=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        approval,
        "get_decrypted_alpaca_credentials",
        AsyncMock(return_value=("paper-key", "paper-secret")),
    )
    monkeypatch.setattr(
        approval,
        "AlpacaClient",
        lambda **_: _ClientContext(client),
    )
    monkeypatch.setattr(
        approval,
        "fetch_market_clock_for",
        AsyncMock(return_value=MarketClock(is_open=market_open)),
    )
    monkeypatch.setattr(
        approval,
        "is_kill_switch_enabled",
        AsyncMock(return_value=False),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: account)
        )
    )
    return db, agent, cycle, client


@pytest.mark.asyncio
async def test_approved_cycle_trade_uses_live_risk_data_before_submission(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    order = SimpleNamespace(status=BrokerOrderStatus.SUBMITTED)
    submit_order = AsyncMock(return_value=order)
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "ALLOW"
    assert decision.policy_token
    assert submitted is order
    client.get_account.assert_awaited_once()
    client.get_market_snapshot.assert_awaited_once_with("AAPL")
    client.get_positions.assert_awaited_once()
    client.get_orders.assert_awaited_once_with(status="all")
    submit_order.assert_awaited_once()
    approved_order = submit_order.await_args.args[0]
    assert approved_order.decision_id == cycle.id
    assert approved_order.symbol == Symbol.AAPL
    assert approved_order.side.value == "BUY"
    assert approved_order.quantity == 1


@pytest.mark.asyncio
async def test_approved_cycle_trade_does_not_submit_when_market_is_closed(monkeypatch):
    db, agent, cycle, _ = _trade_fixtures(monkeypatch, market_open=False)
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "DENY"
    assert submitted is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker_status", "expected_status", "filled_quantity"),
    [
        ("accepted", BrokerOrderStatus.SUBMITTED, "0"),
        ("filled", BrokerOrderStatus.FILLED, "1"),
        ("partially_filled", BrokerOrderStatus.PARTIALLY_FILLED, "0.5"),
    ],
)
async def test_gateway_persists_broker_order_execution_state(
    broker_status, expected_status, filled_quantity
):
    db = SimpleNamespace(
        add=lambda _: None,
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    client = SimpleNamespace(
        submit_order=AsyncMock(
            return_value=SimpleNamespace(
                id="broker-order",
                filled_qty=Decimal(filled_quantity),
                average_fill_price=Decimal("100") if filled_quantity != "0" else None,
                status=broker_status,
            )
        )
    )
    approved_order = ApprovedOrder(
        decision_id=uuid4(),
        policy_token="signed-token",
        client_order_id=f"test-{uuid4()}",
        symbol=Symbol.AAPL,
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    order = await submit_approved_order(approved_order, client, db)

    assert order.status == expected_status
    assert order.filled_quantity == int(Decimal(filled_quantity))
    client.submit_order.assert_awaited_once()
