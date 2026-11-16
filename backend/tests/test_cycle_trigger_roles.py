"""Role checks on agent activity (#176).

Locks in: a VIEWER gets 403 from `POST /agent/cycles/run` and `POST /agent/scheduler/start`
before any agent provisioning, LLM call or broker call; an OPERATOR or ADMIN is
unaffected; Stop stays open to the owner (pausing only reduces risk); and a scheduled
tick skips an owner who is no longer OPERATOR/ADMIN, logged with its own reason.
"""

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import func, select

from app.agent import orchestrator
from app.agent import scheduler as scheduler_service
from app.core import constants
from app.db.enums import ApprovalMode, UserRole
from app.db.models import (
    AgentConfig,
    AgentCycle,
    AgentSchedule,
    KillSwitchEvent,
    PolicySet,
    TradingAccount,
    User,
)
from app.main import app
from app.market_data.alpaca_client import OHLCVBar
from app.risk.market_hours import MarketClock

PASSWORD = "S3cure-Password!"
REASONING = "RSI-14 is 30.0 (oversold). Citing RSI=30.0, SMA20=1.00."


async def _register(client: AsyncClient, email: str, role: UserRole = UserRole.VIEWER) -> str:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    if role is not UserRole.VIEWER:
        await _set_role(email, role)
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _set_role(email: str, role: UserRole) -> None:
    # The token keeps working: get_current_user reloads the user on every request.
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await db.commit()


async def _seed_agent_for(email: str) -> uuid.UUID:
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        account = TradingAccount(
            user_id=user.id,
            provider="ALPACA",
            encrypted_api_key="enc_key",
            encrypted_api_secret="enc_secret",
            is_paper=True,
        )
        db.add(account)
        await db.flush()
        policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
        db.add(policy_set)
        await db.flush()
        agent = AgentConfig(
            account_id=account.id,
            policy_set_id=policy_set.id,
            name="Role Test Agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=ApprovalMode.REQUIRED,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _count(model) -> int:
    async with TestSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def _pause_scheduler(email: str) -> None:
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.scheduler_enabled = False
        await db.commit()


async def _scheduler_enabled(email: str) -> bool:
    async with TestSessionLocal() as db:
        return (
            await db.execute(select(User.scheduler_enabled).where(User.email == email))
        ).scalar_one()


def _bars() -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp="2026-10-05T14:00:00+00:00",
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.0,
            volume=1000.0,
        )
        for _ in range(50)
    ]


class _FakeRedis:
    """Stands in for Redis tick history and dispatch keys (no server needed)."""

    def __init__(self) -> None:
        self.values: dict[str, list[str]] = {}

    def rpush(self, key, value):
        self.values.setdefault(key, []).append(value)

    def ltrim(self, key, _start, _end):
        self.values[key] = self.values[key][-100:]

    def expire(self, *_args):
        pass

    def lrange(self, key, start, end):
        values = self.values.get(key, [])
        return values[start:] if end == -1 else values[start : end + 1]

    def close(self):
        pass


@pytest.fixture
def no_downstream(monkeypatch):
    """Spy on every path a cycle could take to the LLM or the broker."""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=json.dumps({"symbol": "AAPL", "action": "HOLD", "reasoning": REASONING})
    )
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(
        return_value={symbol: _bars() for symbol in constants.DEFAULT_WATCHLIST}
    )
    spies = SimpleNamespace(
        get_llm_client=MagicMock(return_value=llm),
        create_collector=MagicMock(return_value=collector),
        fetch_live_account_data=AsyncMock(side_effect=AssertionError("broker read")),
        evaluate_and_submit=AsyncMock(side_effect=AssertionError("broker submit")),
        llm=llm,
    )
    monkeypatch.setattr(orchestrator, "get_llm_client", spies.get_llm_client)
    monkeypatch.setattr(orchestrator, "create_market_data_collector", spies.create_collector)
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", spies.fetch_live_account_data)
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", spies.evaluate_and_submit)
    return spies


@pytest.fixture
def fake_scheduler(monkeypatch):
    """A running APScheduler stand-in on app.state (ASGITransport skips the lifespan)."""
    instance = SimpleNamespace(running=True, add_job=MagicMock(), get_job=lambda _: None)
    monkeypatch.setattr(app.state, "scheduler", instance, raising=False)
    monkeypatch.setattr(scheduler_service, "_new_redis_client", lambda: _FakeRedis())
    return instance


@pytest.mark.asyncio
async def test_viewer_cannot_trigger_a_cycle(client: AsyncClient, no_downstream):
    """403 before auto-provisioning: no account, agent or cycle row and no LLM/broker call."""
    token = await _register(client, "viewer-run@example.com")

    response = await client.post("/agent/cycles/run", headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient role for this operation"
    assert await _count(TradingAccount) == 0
    assert await _count(AgentConfig) == 0
    assert await _count(AgentCycle) == 0
    no_downstream.get_llm_client.assert_not_called()
    no_downstream.create_collector.assert_not_called()
    no_downstream.fetch_live_account_data.assert_not_awaited()
    no_downstream.evaluate_and_submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_viewer_cannot_trigger_their_existing_agent(client: AsyncClient, no_downstream):
    """A demoted operator with an agent is rejected the same way, by agent_id too."""
    email = "viewer-run-agent@example.com"
    token = await _register(client, email)
    agent_id = await _seed_agent_for(email)

    for url in ("/agent/cycles/run", f"/agent/cycles/run?agent_id={agent_id}"):
        response = await client.post(url, headers=_auth(token))
        assert response.status_code == 403

    assert await _count(AgentCycle) == 0
    no_downstream.get_llm_client.assert_not_called()
    no_downstream.create_collector.assert_not_called()
    no_downstream.llm.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.ADMIN])
async def test_operator_and_admin_can_trigger_a_cycle(
    client: AsyncClient, no_downstream, role: UserRole
):
    no_downstream.fetch_live_account_data.side_effect = None
    no_downstream.fetch_live_account_data.return_value = None  # account unavailable
    token = await _register(client, f"{role.value.lower()}-run@example.com", role)

    response = await client.post("/agent/cycles/run", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["status"] == "HOLD"
    assert await _count(AgentConfig) == 1  # the default agent was auto-provisioned
    assert await _count(AgentCycle) == 1
    no_downstream.llm.generate.assert_awaited()


@pytest.mark.asyncio
async def test_viewer_cannot_start_a_schedule(client: AsyncClient, fake_scheduler):
    email = "viewer-start@example.com"
    token = await _register(client, email)
    agent_id = await _seed_agent_for(email)
    await _pause_scheduler(email)  # stopped earlier; a 403 must not resume it

    response = await client.post(
        "/agent/scheduler/start",
        json={"agent_id": str(agent_id), "interval_minutes": 5},
        headers=_auth(token),
    )

    assert response.status_code == 403
    assert await _count(AgentSchedule) == 0
    assert await _count(KillSwitchEvent) == 0  # the user's scheduler switch was not touched
    assert await _scheduler_enabled(email) is False
    fake_scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.ADMIN])
async def test_operator_and_admin_can_start_a_schedule(
    client: AsyncClient, fake_scheduler, role: UserRole
):
    email = f"{role.value.lower()}-start@example.com"
    token = await _register(client, email, role)
    agent_id = await _seed_agent_for(email)
    await _pause_scheduler(email)

    response = await client.post(
        "/agent/scheduler/start",
        json={"agent_id": str(agent_id), "interval_minutes": 5},
        headers=_auth(token),
    )

    assert response.status_code == 201
    assert response.json()["schedule"]["interval_minutes"] == 5
    assert await _count(AgentSchedule) == 1
    assert await _scheduler_enabled(email) is True
    fake_scheduler.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_demoted_owner_can_still_stop_their_schedule(client: AsyncClient, fake_scheduler):
    """Stop stays open to the owner whatever their role: pausing only reduces risk."""
    email = "demoted-stop@example.com"
    token = await _register(client, email, UserRole.OPERATOR)
    agent_id = await _seed_agent_for(email)
    started = await client.post(
        "/agent/scheduler/start",
        json={"agent_id": str(agent_id), "interval_minutes": 5},
        headers=_auth(token),
    )
    assert started.status_code == 201
    await _set_role(email, UserRole.VIEWER)

    response = await client.post(
        f"/agent/scheduler/{started.json()['schedule']['id']}/stop", headers=_auth(token)
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert await _scheduler_enabled(email) is False


@pytest.mark.asyncio
async def test_demoted_owner_schedule_tick_is_skipped(
    client: AsyncClient, fake_scheduler, monkeypatch, caplog
):
    """A schedule started by an operator who is later demoted runs nothing, and says why."""
    email = "demoted-tick@example.com"
    token = await _register(client, email, UserRole.OPERATOR)
    agent_id = await _seed_agent_for(email)
    started = await client.post(
        "/agent/scheduler/start",
        json={"agent_id": str(agent_id), "interval_minutes": 5},
        headers=_auth(token),
    )
    assert started.status_code == 201
    schedule_id = uuid.UUID(started.json()["schedule"]["id"])

    fake_redis = _FakeRedis()
    monkeypatch.setattr(scheduler_service, "_new_redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_service, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(
        scheduler_service, "is_kill_switch_enabled", AsyncMock(return_value=False)
    )
    market_clock = AsyncMock(return_value=MarketClock(is_open=True))
    monkeypatch.setattr(scheduler_service, "_fetch_market_clock", market_clock)
    sweep = AsyncMock()
    monkeypatch.setattr(scheduler_service, "run_watchlist_cycles", sweep)

    await _set_role(email, UserRole.VIEWER)
    with caplog.at_level(logging.WARNING, logger="app.agent.scheduler"):
        tick = await scheduler_service.run_agent_schedule(schedule_id)

    assert tick is not None
    assert tick.status == "SKIPPED"
    assert tick.reason == "owner_not_operator"
    assert tick.enabled_agents == 1
    assert tick.attempted_agents == 0
    market_clock.assert_not_awaited()  # skipped before any broker call
    sweep.assert_not_awaited()
    assert f"Scheduled cycle skipped for agent {agent_id}" in caplog.text
    assert "is not OPERATOR or ADMIN" in caplog.text
    assert scheduler_service.get_tick_history(schedule_id, limit=1) == [tick]

    # Promoted again, the same schedule runs on its next tick.
    await _set_role(email, UserRole.OPERATOR)
    tick = await scheduler_service.run_agent_schedule(schedule_id)

    assert tick is not None
    assert tick.status == "COMPLETED"
    assert tick.attempted_agents == 1
    sweep.assert_awaited_once()
    assert sweep.await_args.args[0] == agent_id
