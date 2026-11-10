"""Manual override orders are audited (#162).

POST /api/v1/broker/orders is a kill-switch-only human override. Every authorized
attempt now writes exactly one audit_events row (SUBMITTED, BLOCKED or FAILED) naming
the user, the order and how it ended, and the row is committed even when the request
answers 423 or 502. A VIEWER's 403 writes nothing, and no row ever carries the Alpaca
key, secret or the caller's token. Driven through the HTTP client, with the user's real
vault credentials and only the Alpaca POST mocked.
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.alpaca import AlpacaClient
from app.broker.schemas import AlpacaOrderRead
from app.core.constants import (
    MANUAL_ORDER_BLOCKED,
    MANUAL_ORDER_FAILED,
    MANUAL_ORDER_SUBMITTED,
)
from app.db.enums import AuditActorType, UserRole
from app.db.models import AuditEvent, User

PASSWORD = "S3cure-Password!"
# Canary credentials: they must never show up in an audit row.
API_KEY = "AKCANARY162KEYVALUE"
API_SECRET = "canary-162-secret-value-do-not-log"
ORDERS_URL = "/api/v1/broker/orders"


async def _operator(client: AsyncClient, role: UserRole = UserRole.OPERATOR) -> tuple[str, User]:
    """Register a user with the given role and connected (canary) Alpaca credentials."""
    email = f"manual-{uuid.uuid4()}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    token = login.json()["access_token"]
    connect = await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect.status_code == 201
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await db.commit()
    return token, user


async def _audit_rows() -> list[AuditEvent]:
    async with TestSessionLocal() as db:
        return list((await db.execute(select(AuditEvent))).scalars().all())


def _placed(**overrides) -> AlpacaOrderRead:
    return AlpacaOrderRead(
        **{
            "id": "broker-order-1",
            "client_order_id": "manual-order-0001",
            "symbol": "AAPL",
            "qty": Decimal("2"),
            "side": "buy",
            "type": "limit",
            "time_in_force": "day",
            "status": "accepted",
            **overrides,
        }
    )


ORDER = {
    "symbol": "aapl",
    "qty": 2,
    "side": "BUY",
    "type": "LIMIT",
    "time_in_force": "DAY",
    "limit_price": "150.25",
    "client_order_id": "manual-order-0001",
}


@pytest.mark.asyncio
async def test_a_submitted_manual_order_writes_one_submitted_row(client: AsyncClient):
    token, user = await _operator(client)
    submit = AsyncMock(return_value=_placed())

    with patch.object(AlpacaClient, "submit_order", submit):
        response = await client.post(
            ORDERS_URL, json=ORDER, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 201
    assert response.json()["id"] == "broker-order-1"  # the response itself is unchanged
    submit.assert_awaited_once()
    [row] = await _audit_rows()
    assert row.event_type == MANUAL_ORDER_SUBMITTED
    assert row.actor_type == AuditActorType.USER.value
    assert row.actor_id == user.id
    assert row.entity_type == "manual_order"
    assert row.entity_id is None
    assert row.occurred_at is not None
    assert row.details == {
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 2,
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "limit_price": "150.25",
        "client_order_id": "manual-order-0001",
        "outcome": "SUBMITTED",
        "broker_order_id": "broker-order-1",
        "broker_status": "accepted",
    }


@pytest.mark.asyncio
async def test_a_market_order_records_no_limit_price(client: AsyncClient):
    token, _ = await _operator(client)
    market = {"symbol": "NVDA", "qty": 1, "side": "SELL"}

    with patch.object(AlpacaClient, "submit_order", AsyncMock(return_value=_placed())):
        response = await client.post(
            ORDERS_URL, json=market, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 201
    [row] = await _audit_rows()
    assert row.details["order_type"] == "MARKET"
    assert row.details["limit_price"] is None
    assert row.details["side"] == "SELL"


@pytest.mark.asyncio
async def test_a_kill_switch_block_writes_one_committed_blocked_row(
    client: AsyncClient, monkeypatch
):
    token, user = await _operator(client)
    monkeypatch.setattr(
        "app.broker.router.is_kill_switch_enabled", AsyncMock(return_value=True)
    )
    submit = AsyncMock()

    with patch.object(AlpacaClient, "submit_order", submit):
        response = await client.post(
            ORDERS_URL, json=ORDER, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 423
    assert response.json()["detail"] == "Order submission blocked by the global kill switch"
    submit.assert_not_awaited()
    # Read back in a fresh session: the row was committed despite the error response.
    [row] = await _audit_rows()
    assert row.event_type == MANUAL_ORDER_BLOCKED
    assert row.actor_id == user.id
    assert row.details["outcome"] == "BLOCKED"
    assert row.details["symbol"] == "AAPL"
    assert row.details["quantity"] == 2
    assert "broker_order_id" not in row.details


@pytest.mark.asyncio
async def test_a_broker_error_writes_one_committed_failed_row(client: AsyncClient):
    token, user = await _operator(client)
    error = httpx.HTTPStatusError(
        "Client error '422 Unprocessable Entity' for url "
        "'https://paper-api.alpaca.markets/v2/orders'",
        request=httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders"),
        response=httpx.Response(422),
    )

    with patch.object(AlpacaClient, "submit_order", AsyncMock(side_effect=error)):
        response = await client.post(
            ORDERS_URL, json=ORDER, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 502
    assert response.json()["detail"] == f"Alpaca API error: {error}"
    [row] = await _audit_rows()
    assert row.event_type == MANUAL_ORDER_FAILED
    assert row.actor_id == user.id
    assert row.details["outcome"] == "FAILED"
    assert row.details["broker_error"] == str(error)
    assert "broker_order_id" not in row.details


@pytest.mark.asyncio
async def test_a_viewer_gets_403_and_writes_no_row(client: AsyncClient):
    token, _ = await _operator(client, role=UserRole.VIEWER)
    submit = AsyncMock()

    with patch.object(AlpacaClient, "submit_order", submit):
        response = await client.post(
            ORDERS_URL, json=ORDER, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 403
    submit.assert_not_awaited()
    assert await _audit_rows() == []


@pytest.mark.asyncio
async def test_audit_details_never_contain_credentials_or_tokens(
    client: AsyncClient, monkeypatch
):
    """Not even when a broker error message echoes the key and secret back."""
    token, _ = await _operator(client)
    headers = {"Authorization": f"Bearer {token}"}
    leaky = RuntimeError(f"rejected key {API_KEY} with secret {API_SECRET}")

    with patch.object(AlpacaClient, "submit_order", AsyncMock(return_value=_placed())):
        assert (await client.post(ORDERS_URL, json=ORDER, headers=headers)).status_code == 201
    with patch.object(AlpacaClient, "submit_order", AsyncMock(side_effect=leaky)):
        assert (await client.post(ORDERS_URL, json=ORDER, headers=headers)).status_code == 502
    monkeypatch.setattr(
        "app.broker.router.is_kill_switch_enabled", AsyncMock(return_value=True)
    )
    assert (await client.post(ORDERS_URL, json=ORDER, headers=headers)).status_code == 423

    rows = await _audit_rows()
    assert sorted(row.event_type for row in rows) == sorted(
        [MANUAL_ORDER_SUBMITTED, MANUAL_ORDER_FAILED, MANUAL_ORDER_BLOCKED]
    )
    for row in rows:
        text = json.dumps(row.details)
        for secret in (API_KEY, API_SECRET, token):
            assert secret not in text
        assert not {"api_key", "api_secret", "policy_token", "token"} & set(row.details)
    failed = next(row for row in rows if row.event_type == MANUAL_ORDER_FAILED)
    assert failed.details["broker_error"] == (
        "rejected key [REDACTED] with secret [REDACTED]"
    )


@pytest.mark.asyncio
async def test_a_failed_audit_commit_does_not_hide_a_placed_order(client: AsyncClient):
    """The order is already at Alpaca: answering 500 would invite a duplicate retry."""
    token, _ = await _operator(client)
    submit = AsyncMock(return_value=_placed())
    broken_commit = AsyncMock(side_effect=SQLAlchemyError("database unavailable"))

    with (
        patch.object(AlpacaClient, "submit_order", submit),
        patch.object(AsyncSession, "commit", broken_commit),
    ):
        response = await client.post(
            ORDERS_URL, json=ORDER, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 201
    assert response.json()["id"] == "broker-order-1"
    broken_commit.assert_awaited_once()
    assert await _audit_rows() == []  # rolled back and logged, not half-written
