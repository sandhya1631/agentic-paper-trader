"""An AUTO cycle's final snapshot is actually written to the database (#186).

`snapshot_json` is a plain JSON column, so SQLAlchemy doesn't track in-place mutation:
reassigning the same dict after the AUTO branch's EXECUTING commit was never detected,
and the stored snapshot stayed at `execution_status == "EXECUTING"` with no
`policy_evaluation`. These tests drive the REAL orchestrator, approval path, policy
engine and broker gateway against Postgres (only Alpaca's network calls are faked),
then read the cycle back in a FRESH session, so they see what was committed rather
than the in-memory object.
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from sqlalchemy import select

from app.agent import approval
from app.agent.orchestrator import run_agent_cycle
from app.broker import gateway as gateway_module
from app.db.enums import ApprovalMode, BrokerOrderStatus, CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentConfig, AgentCycle, BrokerOrder, PolicySet, TradingAccount, User
from app.risk.market_hours import MarketClock

BUY = {"symbol": "AAPL", "action": "BUY", "quantity": 1, "reasoning": "RSI is 35."}


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_):
        return None


def _fake_alpaca(
    monkeypatch,
    *,
    market_open: bool = True,
    gateway_kill_switch: bool = False,
    submit_order: AsyncMock | None = None,
) -> SimpleNamespace:
    """Fake only Alpaca's network boundary; the policies and the gateway stay real."""
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
        submit_order=submit_order
        or AsyncMock(
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
        approval,
        "fetch_market_clock_for",
        AsyncMock(return_value=MarketClock(is_open=market_open)),
    )
    # P-002 reads the kill switch in the approval path; the gateway re-checks it. Turning
    # it on only at the gateway models a switch flipped between evaluation and submit.
    monkeypatch.setattr(approval, "is_kill_switch_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        gateway_module, "is_kill_switch_enabled", AsyncMock(return_value=gateway_kill_switch)
    )
    return client


async def _seed_auto_agent() -> uuid.UUID:
    async with TestSessionLocal() as db:
        user = User(
            email=f"auto-snapshot-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            role=UserRole.OPERATOR,
        )
        db.add(user)
        await db.flush()
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
            name="AUTO snapshot agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=ApprovalMode.AUTO,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _run_auto_buy(agent_id: uuid.UUID) -> uuid.UUID:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=json.dumps(BUY))
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    async with TestSessionLocal() as db:
        cycle = await run_agent_cycle(
            agent_id,
            db,
            trigger=CycleTrigger.MANUAL,
            llm_client=llm,
            collector=collector,
            account_data=None,
        )
        return cycle.id


async def _stored_cycle(cycle_id: uuid.UUID) -> AgentCycle:
    """The cycle as committed, read through a brand-new session."""
    async with TestSessionLocal() as db:
        return (
            await db.execute(select(AgentCycle).where(AgentCycle.id == cycle_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_submitted_auto_cycle_persists_final_snapshot(db_session, monkeypatch):
    _fake_alpaca(monkeypatch)
    agent_id = await _seed_auto_agent()

    cycle = await _stored_cycle(await _run_auto_buy(agent_id))

    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.snapshot_json["execution_status"] == "ORDER_SUBMITTED"
    evaluation = cycle.snapshot_json["policy_evaluation"]
    assert evaluation["decision"] == "ALLOW"
    assert "policy_token" not in evaluation
    assert "policy_token" not in json.dumps(cycle.snapshot_json)


@pytest.mark.asyncio
async def test_policy_denied_auto_cycle_persists_final_snapshot(db_session, monkeypatch):
    # The market is closed, so P-003 denies and nothing reaches the broker.
    client = _fake_alpaca(monkeypatch, market_open=False)
    agent_id = await _seed_auto_agent()

    cycle = await _stored_cycle(await _run_auto_buy(agent_id))

    assert cycle.status == CycleStatus.REJECTED
    assert cycle.failure_code == "POLICY_DENIED"
    assert cycle.snapshot_json["execution_status"] == "POLICY_DENIED"
    evaluation = cycle.snapshot_json["policy_evaluation"]
    assert evaluation["decision"] == "DENY"
    assert "policy_token" not in evaluation
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_broker_rejected_auto_cycle_persists_final_snapshot(db_session, monkeypatch):
    rejected = AsyncMock(
        return_value=SimpleNamespace(
            id="broker-order-id",
            filled_qty=Decimal("0"),
            average_fill_price=None,
            status="rejected",
        )
    )
    _fake_alpaca(monkeypatch, submit_order=rejected)
    agent_id = await _seed_auto_agent()

    cycle = await _stored_cycle(await _run_auto_buy(agent_id))

    assert cycle.status == CycleStatus.REJECTED
    assert cycle.failure_code == "BROKER_REJECTED"
    assert cycle.snapshot_json["execution_status"] == "ORDER_REJECTED"
    assert "policy_token" not in cycle.snapshot_json["policy_evaluation"]


@pytest.mark.asyncio
async def test_gateway_kill_switch_auto_cycle_persists_final_snapshot(db_session, monkeypatch):
    client = _fake_alpaca(monkeypatch, gateway_kill_switch=True)
    agent_id = await _seed_auto_agent()

    cycle = await _stored_cycle(await _run_auto_buy(agent_id))

    assert cycle.status == CycleStatus.REJECTED
    assert cycle.failure_code == "KILL_SWITCH_ENABLED"
    assert cycle.snapshot_json["execution_status"] == "KILL_SWITCH_ENABLED"
    client.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_auto_execution_persists_reconciliation_snapshot(
    db_session, monkeypatch
):
    # A non-HTTP error mid-submit: the gateway already persisted the BrokerOrder, so
    # whether Alpaca received the POST is unknown and the cycle must be reconciled.
    lost = AsyncMock(side_effect=RuntimeError("connection reset mid-submit"))
    _fake_alpaca(monkeypatch, submit_order=lost)
    agent_id = await _seed_auto_agent()

    cycle_id = await _run_auto_buy(agent_id)
    cycle = await _stored_cycle(cycle_id)

    assert cycle.status == CycleStatus.RECONCILING
    assert cycle.failure_code == "ORDER_EXECUTION_UNCERTAIN"
    assert cycle.snapshot_json["execution_status"] == "RECONCILIATION_REQUIRED"
    async with TestSessionLocal() as db:
        order = (
            await db.execute(select(BrokerOrder).where(BrokerOrder.decision_id == cycle_id))
        ).scalar_one()
    assert order.status == BrokerOrderStatus.PENDING_SUBMISSION
