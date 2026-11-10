from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alpaca_order_book import NOTIONAL_ORDER_JSON, FakeOrderBook, make_order
from test_broker_positions_orders import _mock_http_get

from app.agent import approval
from app.broker.alpaca import AlpacaClient
from app.broker.gateway import submit_approved_order
from app.core.config import Settings
from app.core.constants import ALPACA_ORDERS_PAGE_LIMIT
from app.db.enums import (
    ApprovalMode,
    BrokerOrderStatus,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
)
from app.risk.daily_limits import trading_day_start
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
    # P-010 reads every open order; P-012 reads every order since the US/Eastern day began.
    open_call, today_call = client.get_orders.await_args_list
    assert open_call.kwargs["status"] == "open"
    assert open_call.kwargs["after"] is None
    assert open_call.kwargs["limit"] == ALPACA_ORDERS_PAGE_LIMIT
    assert today_call.kwargs["status"] == "all"
    assert today_call.kwargs["after"] < trading_day_start() <= datetime.now(UTC)
    assert today_call.kwargs["limit"] == ALPACA_ORDERS_PAGE_LIMIT
    submit_order.assert_awaited_once()
    approved_order = submit_order.await_args.args[0]
    assert approved_order.decision_id == cycle.id
    assert approved_order.symbol == Symbol.AAPL
    assert approved_order.side.value == "BUY"
    assert approved_order.quantity == 1


def _p005(decision):
    return next(e for e in decision.evaluations if e.policy_id == "P-005")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored_watchlist",
    [
        pytest.param(["aapl", "AAPL", "nvda"], id="lowercase-and-duplicates"),
        pytest.param(["aapl", "nvda"], id="lowercase-only"),
    ],
)
async def test_p005_allows_a_symbol_in_a_watchlist_stored_unnormalized(
    monkeypatch, stored_watchlist
):
    """#179: P-005 checks the canonical watchlist the prompt and the sweep use, so a
    row stored un-normalized (an earlier SQL edit) still allows AAPL."""
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    agent.watchlist = stored_watchlist
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(
        db, agent, cycle, submit=False
    )

    assert _p005(decision).outcome == "PASS"
    assert _p005(decision).evidence["watchlist"] == ["AAPL", "NVDA"]
    assert decision.decision == "ALLOW"
    assert submitted is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_p005_denies_a_symbol_outside_a_watchlist_stored_unnormalized(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    agent.watchlist = ["aapl", "AAPL", "nvda"]
    cycle.proposal_json["symbol"] = "MSFT"
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(
        db, agent, cycle, submit=False
    )

    assert _p005(decision).outcome == "FAIL"
    assert _p005(decision).evidence["watchlist"] == ["AAPL", "NVDA"]
    assert decision.decision == "DENY"
    assert decision.policy_token is None
    assert submitted is None
    submit_order.assert_not_awaited()


def _open_order(symbol: str, status: str = "accepted"):
    return SimpleNamespace(symbol=symbol, status=status, submitted_at=None)


@pytest.mark.asyncio
async def test_open_order_conflict_denies_real_open_order_for_same_symbol(monkeypatch):
    """P-010 wired with real Alpaca order data (Story 3.2.2 / Issue #73)."""
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_orders = AsyncMock(return_value=[_open_order("AAPL")])
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "DENY"
    assert submitted is None
    submit_order.assert_not_awaited()
    failed_ids = {e.policy_id for e in decision.evaluations if e.outcome == "FAIL"}
    assert "P-010" in failed_ids


@pytest.mark.asyncio
async def test_open_order_for_a_different_symbol_does_not_block(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_orders = AsyncMock(return_value=[_open_order("NVDA")])
    order = SimpleNamespace(status=BrokerOrderStatus.SUBMITTED)
    submit_order = AsyncMock(return_value=order)
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "ALLOW"
    assert submitted is order
    submit_order.assert_awaited_once()


def _orders_by_status(*, open_orders, all_orders):
    """A get_orders double answering like Alpaca: ``status="open"`` lists open orders only."""

    async def get_orders(status="all", **_kwargs):
        return list(open_orders if status == "open" else all_orders)

    return AsyncMock(side_effect=get_orders)


@pytest.mark.asyncio
async def test_filled_order_for_same_symbol_does_not_block(monkeypatch):
    """A terminal-status order (in the history, not the open list) must not block."""
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_orders = _orders_by_status(
        open_orders=[], all_orders=[_open_order("AAPL", status="filled")]
    )
    order = SimpleNamespace(status=BrokerOrderStatus.SUBMITTED)
    submit_order = AsyncMock(return_value=order)
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "ALLOW"
    assert submitted is order
    submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_order_id_is_deterministic_per_cycle(monkeypatch):
    """A retried/raced call for the SAME cycle must reuse one client_order_id (#73).

    Otherwise each call gets its own random id and the gateway's existing
    dedup-by-client_order_id (#63) never sees a collision to catch.
    """
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    submit_order = AsyncMock(return_value=SimpleNamespace(status=BrokerOrderStatus.SUBMITTED))
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)
    await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    first_call, second_call = submit_order.await_args_list
    first_id = first_call.args[0].client_order_id
    second_id = second_call.args[0].client_order_id
    assert first_id == second_id == str(cycle.id)


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


# --- notional orders and complete order lists (#177) ---


def _real_order_parsing(order_json):
    """AlpacaClient.get_orders itself (the real parser), over a mocked HTTP response."""
    http_client = _mock_http_get([order_json])
    return AlpacaClient(api_key="k", api_secret="s", client=http_client).get_orders


@pytest.mark.asyncio
async def test_a_notional_order_does_not_block_the_policy_evaluation(monkeypatch):
    """Alpaca's `qty: null` used to raise inside get_orders, so no decision was ever made."""
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    notional_order = {**NOTIONAL_ORDER_JSON, "submitted_at": datetime.now(UTC).isoformat()}
    client.get_orders = _real_order_parsing(notional_order)
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(
        db, agent, cycle, submit=False
    )

    assert decision.decision == "ALLOW"  # the MSFT notional order doesn't touch AAPL
    assert decision.evaluations  # every policy ran
    assert submitted is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_open_notional_order_for_the_symbol_still_trips_p010(monkeypatch):
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    client.get_orders = _real_order_parsing({**NOTIONAL_ORDER_JSON, "symbol": "AAPL"})
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    assert decision.decision == "DENY"
    assert "P-010" in {e.policy_id for e in decision.evaluations if e.outcome == "FAIL"}
    assert submitted is None
    submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_orders_today_counts_beyond_the_old_50_order_window(monkeypatch):
    """P-012 used to count only the 50 most recent orders; now it sees all of today's."""
    db, agent, cycle, client = _trade_fixtures(monkeypatch, market_open=True)
    monkeypatch.setattr(
        approval, "get_settings", lambda: Settings(_env_file=None, max_orders_per_day=55)
    )
    now = datetime.now(UTC)
    day_start = trading_day_start(now)
    todays = [
        make_order(
            f"today-{i}",
            symbol="MSFT",
            status="filled",
            submitted_at=now - (now - day_start) * (i + 1) / 62,
        )
        for i in range(60)
    ]
    yesterdays = [
        make_order(
            f"yesterday-{i}",
            symbol="MSFT",
            status="filled",
            submitted_at=day_start - timedelta(hours=i + 1),
        )
        for i in range(5)
    ]
    book = FakeOrderBook(todays + yesterdays)
    client.get_orders = book.get_orders
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    assert len(await book.get_orders(status="all", limit=50)) == 50  # the old, capped view
    decision, submitted = await approval.evaluate_and_submit_cycle_trade(db, agent, cycle)

    p012 = next(e for e in decision.evaluations if e.policy_id == "P-012")
    assert p012.outcome == "FAIL"
    assert p012.evidence["orders_today"] == 60  # yesterday's 5 are not counted
    assert decision.decision == "DENY"
    assert submitted is None
    submit_order.assert_not_awaited()
