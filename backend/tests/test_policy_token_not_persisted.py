"""The signed policy_token is never stored or returned (#175).

These drive the REAL decision path end to end against Postgres: the orchestrator,
app.agent.approval.evaluate_and_submit_cycle_trade, the policy engine and the REAL
broker gateway. Only the Alpaca network boundary is faked. Each test proves the token
still reaches the gateway in memory (``ApprovedOrder.policy_token``) while no stored
column and no HTTP response ever contains it.
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select, text

from app.agent import approval
from app.agent.orchestrator import run_agent_cycle
from app.broker import gateway as gateway_module
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentConfig, PolicySet, TradingAccount, User
from app.risk.market_hours import MarketClock

TOKEN_KEY = "policy_token"
BUY = {"symbol": "AAPL", "action": "BUY", "quantity": 1, "reasoning": "RSI is 35."}


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_):
        return None


def _fake_broker(monkeypatch) -> tuple[SimpleNamespace, list]:
    """Fake only Alpaca's network calls; keep the policies and the real gateway.

    Returns the fake client and a list that collects every ApprovedOrder handed to
    the gateway.
    """
    client = SimpleNamespace(
        get_account=AsyncMock(
            return_value=SimpleNamespace(
                equity=Decimal("100000"), cash=Decimal("50000"), last_equity=Decimal("100000")
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
        submit_order=AsyncMock(
            return_value=SimpleNamespace(
                id="broker-order-id",
                filled_qty=Decimal("0"),
                average_fill_price=None,
                status="accepted",
            )
        ),
    )
    monkeypatch.setattr(
        approval,
        "get_decrypted_alpaca_credentials",
        AsyncMock(return_value=("paper-key", "paper-secret")),
    )
    monkeypatch.setattr(approval, "AlpacaClient", lambda **_: _ClientContext(client))
    monkeypatch.setattr(
        approval, "fetch_market_clock_for", AsyncMock(return_value=MarketClock(is_open=True))
    )
    monkeypatch.setattr(approval, "is_kill_switch_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(gateway_module, "is_kill_switch_enabled", AsyncMock(return_value=False))

    gateway_orders: list = []
    real_submit = approval.submit_approved_order

    async def spy_submit(approved_order, alpaca_client, db):
        gateway_orders.append(approved_order)
        return await real_submit(approved_order, alpaca_client, db)

    monkeypatch.setattr(approval, "submit_approved_order", spy_submit)
    return client, gateway_orders


async def _register_and_login(client: AsyncClient, email: str) -> str:
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


async def _seed_operator_agent(email: str, mode: ApprovalMode) -> uuid.UUID:
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = UserRole.OPERATOR
        account = TradingAccount(
            user_id=user.id,
            provider="ALPACA",
            encrypted_api_key="enc_key",
            encrypted_api_secret="enc_secret",
            is_paper=True,
            is_connected=True,
        )
        db.add(account)
        await db.flush()
        policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
        db.add(policy_set)
        await db.flush()
        agent = AgentConfig(
            account_id=account.id,
            policy_set_id=policy_set.id,
            name="Token Agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=mode,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _run_buy_cycle(agent_id: uuid.UUID):
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=json.dumps(BUY))
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    async with TestSessionLocal() as db:
        return await run_agent_cycle(
            agent_id,
            db,
            trigger=CycleTrigger.MANUAL,
            llm_client=llm,
            collector=collector,
            account_data=None,
        )


async def _stored_policy_json() -> list[str]:
    """Every stored snapshot/policy column of cycles and approvals, as raw JSON text."""
    async with TestSessionLocal() as db:
        cycles = await db.execute(
            text("SELECT snapshot_json::text, policy_result_json::text FROM agent_cycles")
        )
        approvals = await db.execute(text("SELECT policy_result_json::text FROM cycle_approvals"))
        return [value for row in [*cycles, *approvals] for value in row if value is not None]


async def _assert_cycle_reads_have_no_token(client: AsyncClient, auth: dict, cycle_id) -> dict:
    """The cycle list and detail never contain the token; returns the detail body."""
    listing = await client.get("/agent/cycles", headers=auth)
    detail = await client.get(f"/agent/cycles/{cycle_id}", headers=auth)
    for response in (listing, detail):
        assert response.status_code == 200
        assert TOKEN_KEY not in response.text
    # The decision itself is still recorded and returned, just without the token.
    assert listing.json()["items"][0]["policy_result_json"]["decision"] == "ALLOW"
    assert detail.json()["policy_result_json"]["decision"] == "ALLOW"
    return detail.json()


@pytest.mark.asyncio
async def test_auto_cycle_never_stores_or_returns_the_policy_token(
    client: AsyncClient, monkeypatch
):
    email = "auto-token@example.com"
    token = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {token}"}
    agent_id = await _seed_operator_agent(email, ApprovalMode.AUTO)
    broker, gateway_orders = _fake_broker(monkeypatch)

    cycle = await _run_buy_cycle(agent_id)

    assert cycle.status == CycleStatus.COMPLETED
    # The gateway still received the signed token in memory, and submitted once.
    assert len(gateway_orders) == 1
    assert gateway_orders[0].policy_token
    broker.submit_order.assert_awaited_once()

    stored = await _stored_policy_json()
    assert len(stored) == 2  # cycle snapshot + cycle policy result
    assert all(TOKEN_KEY not in value for value in stored)

    await _assert_cycle_reads_have_no_token(client, auth, cycle.id)


@pytest.mark.asyncio
async def test_approve_never_stores_or_returns_the_policy_token(client: AsyncClient, monkeypatch):
    email = "approve-token@example.com"
    token = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {token}"}
    agent_id = await _seed_operator_agent(email, ApprovalMode.REQUIRED)
    broker, gateway_orders = _fake_broker(monkeypatch)

    parked = await _run_buy_cycle(agent_id)
    assert parked.status == CycleStatus.PENDING_APPROVAL
    assert gateway_orders == []  # the preview is a dry run

    pending = await client.get("/agent/approvals/pending", headers=auth)
    assert pending.status_code == 200
    assert TOKEN_KEY not in pending.text
    [item] = pending.json()
    assert item["approval"]["policy_result_json"]["decision"] == "ALLOW"

    approve = await client.post(
        f"/agent/approvals/{item['approval']['id']}/approve",
        json={"comment": "go"},
        headers=auth,
    )

    assert approve.status_code == 200
    assert approve.json()["status"] == CycleStatus.COMPLETED.value
    assert TOKEN_KEY not in approve.text
    # The gateway still received the signed token in memory, and submitted once.
    assert len(gateway_orders) == 1
    assert gateway_orders[0].policy_token
    broker.submit_order.assert_awaited_once()

    stored = await _stored_policy_json()
    assert len(stored) == 3  # cycle snapshot + cycle policy result + approval policy result
    assert all(TOKEN_KEY not in value for value in stored)

    detail = await _assert_cycle_reads_have_no_token(client, auth, parked.id)
    assert detail["snapshot_json"]["policy_evaluation"]["decision"] == "ALLOW"
    assert detail["approval"]["policy_result_json"]["decision"] == "ALLOW"


@pytest.mark.asyncio
async def test_reject_response_has_no_policy_token(client: AsyncClient, monkeypatch):
    email = "reject-token@example.com"
    token = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {token}"}
    agent_id = await _seed_operator_agent(email, ApprovalMode.REQUIRED)
    broker, gateway_orders = _fake_broker(monkeypatch)

    parked = await _run_buy_cycle(agent_id)
    assert parked.approval is not None

    reject = await client.post(
        f"/agent/approvals/{parked.approval.id}/reject", json={"comment": "no"}, headers=auth
    )

    assert reject.status_code == 200
    assert reject.json()["status"] == CycleStatus.REJECTED.value
    assert TOKEN_KEY not in reject.text
    assert gateway_orders == []
    broker.submit_order.assert_not_awaited()
    assert all(TOKEN_KEY not in value for value in await _stored_policy_json())
