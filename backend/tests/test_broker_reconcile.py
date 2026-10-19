"""Tests for confirming/restoring order status by client_order_id (Story 1.3.2 / Issue #63)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.alpaca import AlpacaClient
from app.broker.gateway import get_order_by_client_id, submit_approved_order
from app.broker.reconcile import reconcile_order
from app.db.enums import (
    ApprovalMode,
    BrokerOrderStatus,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
    UserRole,
)
from app.db.models import AgentConfig, AgentCycle, BrokerOrder, PolicySet, TradingAccount, User
from app.schemas import ApprovedOrder


async def _make_cycle(db_session: AsyncSession) -> AgentCycle:
    user = User(email=f"rec-{uuid4()}@example.com", password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db_session.add(account)
    await db_session.flush()

    policy_set = PolicySet(version=f"P-{uuid4()}", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()

    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Reconcile Test Agent",
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db_session.add(agent)
    await db_session.flush()

    cycle = AgentCycle(agent_id=agent.id)
    db_session.add(cycle)
    await db_session.commit()
    await db_session.refresh(cycle)
    return cycle


def _approved_order(decision_id, client_order_id="client-order-123") -> ApprovedOrder:
    return ApprovedOrder(
        decision_id=decision_id,
        policy_token="signed-token",
        client_order_id=client_order_id,
        symbol=Symbol.AAPL,
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _mock_http(get_json=None, get_status=200, post_json=None):
    http = AsyncMock(spec=httpx.AsyncClient)
    http.is_closed = False

    get_response = MagicMock()
    get_response.status_code = get_status
    get_response.raise_for_status = MagicMock()
    if get_json is not None:
        get_response.json.return_value = get_json
    http.get = AsyncMock(return_value=get_response)

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    if post_json is not None:
        post_response.json.return_value = post_json
    http.post = AsyncMock(return_value=post_response)

    return http


def _submitted_order_json(client_order_id, order_id="alpaca-order-1", status="accepted"):
    return {
        "id": order_id,
        "client_order_id": client_order_id,
        "symbol": "AAPL",
        "qty": "10",
        "filled_qty": "0",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": status,
    }


@pytest.mark.asyncio
async def test_lookup_status_by_client_order_id_returns_row(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    http = _mock_http(post_json=_submitted_order_json("client-order-123"))
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    submitted = await submit_approved_order(_approved_order(cycle.id), client, db_session)

    found = await get_order_by_client_id(db_session, "client-order-123")

    assert found is not None
    assert found.id == submitted.id


@pytest.mark.asyncio
async def test_reconcile_updates_status_from_broker(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    order = BrokerOrder(
        decision_id=cycle.id,
        client_order_id="client-order-456",
        status=BrokerOrderStatus.PENDING_SUBMISSION,
        symbol="AAPL",
        side=OrderSide.BUY,
        requested_quantity=10,
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    http = _mock_http(
        get_json=_submitted_order_json(
            "client-order-456", order_id="alpaca-order-999", status="filled"
        )
    )
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    reconciled = await reconcile_order(db_session, client, order)

    assert reconciled.status == BrokerOrderStatus.FILLED
    assert reconciled.broker_order_id == "alpaca-order-999"


@pytest.mark.asyncio
async def test_duplicate_submit_same_client_id_does_not_create_second_order(
    db_session: AsyncSession,
):
    cycle = await _make_cycle(db_session)
    approved = _approved_order(cycle.id)

    first_http = _mock_http(post_json=_submitted_order_json("client-order-123"))
    first_client = AlpacaClient(api_key="k", api_secret="s", client=first_http)
    first = await submit_approved_order(approved, first_client, db_session)

    # Second attempt: reconcile finds the order Alpaca already has.
    second_http = _mock_http(get_json=_submitted_order_json("client-order-123"))
    second_client = AlpacaClient(api_key="k", api_secret="s", client=second_http)
    second = await submit_approved_order(approved, second_client, db_session)

    assert second.id == first.id
    second_http.post.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_when_broker_has_no_such_order_allows_resubmit(
    db_session: AsyncSession,
):
    cycle = await _make_cycle(db_session)
    # Simulate a prior crash: the row was persisted but the POST never landed.
    stuck_order = BrokerOrder(
        decision_id=cycle.id,
        client_order_id="client-order-789",
        status=BrokerOrderStatus.PENDING_SUBMISSION,
        symbol="AAPL",
        side=OrderSide.BUY,
        requested_quantity=10,
    )
    db_session.add(stuck_order)
    await db_session.commit()
    await db_session.refresh(stuck_order)

    http = _mock_http(
        get_status=404, post_json=_submitted_order_json("client-order-789")
    )
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    result = await submit_approved_order(
        _approved_order(cycle.id, client_order_id="client-order-789"), client, db_session
    )

    assert result.id == stuck_order.id
    assert result.status == BrokerOrderStatus.SUBMITTED
    http.post.assert_called_once()
