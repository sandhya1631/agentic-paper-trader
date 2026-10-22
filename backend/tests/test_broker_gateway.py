"""Tests for the broker gateway: submit an approved paper order (Story 1.3.1 / Issue #62)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.alpaca import AlpacaClient
from app.broker.gateway import (
    ApprovedOrderRequiredError,
    TradingDisabledError,
    submit_approved_order,
)
from app.broker.schemas import OrderCreate
from app.db.enums import (
    ApprovalMode,
    BrokerOrderStatus,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
    UserRole,
)
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.schemas import ApprovedOrder


async def _make_cycle(db_session: AsyncSession) -> AgentCycle:
    user = User(email=f"gw-{uuid4()}@example.com", password_hash="hash", role=UserRole.ADMIN)
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
        name="Gateway Test Agent",
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


def _approved_order(decision_id) -> ApprovedOrder:
    return ApprovedOrder(
        decision_id=decision_id,
        policy_token="signed-token",
        client_order_id="client-order-123",
        symbol=Symbol.AAPL,
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _mock_http_post(json_body=None, raise_error: Exception | None = None):
    response = MagicMock()
    response.raise_for_status = MagicMock(
        side_effect=raise_error if isinstance(raise_error, httpx.HTTPStatusError) else None
    )
    if json_body is not None:
        response.json.return_value = json_body
    http = AsyncMock(spec=httpx.AsyncClient)
    if raise_error is not None and not isinstance(raise_error, httpx.HTTPStatusError):
        http.post = AsyncMock(side_effect=raise_error)
    else:
        http.post = AsyncMock(return_value=response)
    http.is_closed = False
    return http


@pytest.mark.asyncio
async def test_gateway_rejects_non_approved_input(db_session: AsyncSession):
    await _make_cycle(db_session)
    http = _mock_http_post()
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    not_approved = OrderCreate(
        symbol="AAPL", qty=10, side=OrderSide.BUY, client_order_id="not-approved-order"
    )

    with pytest.raises(ApprovedOrderRequiredError):
        await submit_approved_order(not_approved, client, db_session)

    http.post.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_persists_client_and_broker_ids(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    alpaca_response = {
        "id": "alpaca-order-1",
        "client_order_id": "client-order-123",
        "symbol": "AAPL",
        "qty": "10",
        "filled_qty": "0",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": "accepted",
    }
    http = _mock_http_post(json_body=alpaca_response)
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    order = await submit_approved_order(_approved_order(cycle.id), client, db_session)

    assert order.client_order_id == "client-order-123"
    assert order.broker_order_id == "alpaca-order-1"
    assert order.status == BrokerOrderStatus.SUBMITTED


@pytest.mark.asyncio
async def test_gateway_sends_client_order_id_in_payload(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    alpaca_response = {
        "id": "alpaca-order-2",
        "client_order_id": "client-order-123",
        "symbol": "AAPL",
        "qty": "10",
        "filled_qty": "0",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": "accepted",
    }
    http = _mock_http_post(json_body=alpaca_response)
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    await submit_approved_order(_approved_order(cycle.id), client, db_session)

    _, kwargs = http.post.call_args
    assert kwargs["json"]["client_order_id"] == "client-order-123"


@pytest.mark.asyncio
async def test_gateway_marks_rejected_on_broker_error(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    error = httpx.HTTPStatusError(
        "422 error", request=MagicMock(), response=MagicMock(status_code=422)
    )
    http = _mock_http_post(raise_error=error)
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    order = await submit_approved_order(_approved_order(cycle.id), client, db_session)

    assert order.status == BrokerOrderStatus.REJECTED
    assert order.rejection_reason is not None
    assert order.broker_order_id is None


@pytest.mark.asyncio
async def test_gateway_marks_rejected_on_network_error(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    http = _mock_http_post(raise_error=httpx.ConnectError("connection refused"))
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    order = await submit_approved_order(_approved_order(cycle.id), client, db_session)

    assert order.status == BrokerOrderStatus.REJECTED


@pytest.mark.asyncio
async def test_gateway_rejects_missing_policy_token(db_session: AsyncSession):
    cycle = await _make_cycle(db_session)
    http = _mock_http_post()
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    approved = _approved_order(cycle.id)
    approved.policy_token = ""

    with pytest.raises(ApprovedOrderRequiredError):
        await submit_approved_order(approved, client, db_session)

    http.post.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_enforces_global_kill_switch(monkeypatch):
    http = _mock_http_post()
    client = AlpacaClient(api_key="k", api_secret="s", client=http)
    db = SimpleNamespace()
    monkeypatch.setattr(
        "app.broker.gateway.is_kill_switch_enabled",
        AsyncMock(return_value=True),
    )

    with pytest.raises(TradingDisabledError, match="kill switch"):
        await submit_approved_order(_approved_order(uuid4()), client, db)

    http.post.assert_not_called()
