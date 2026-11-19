"""POST /agent/scheduler/start writes AGENT_SCHEDULE_UPDATED to audit_events (#216).

One row in the same commit as the schedule change, naming the actor, the schedule and
its agent, with the old and new `interval_minutes` / `enabled` (null before the
schedule existed). A start that changes nothing, and a rejected start, write none.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.agent import scheduler as scheduler_service
from app.agent.router import start_agent_schedule
from app.core.constants import AGENT_SCHEDULE_UPDATED
from app.db.enums import UserRole
from app.db.models import (
    AgentConfig,
    AgentSchedule,
    AuditEvent,
    KillSwitchEvent,
    PolicySet,
    TradingAccount,
    User,
)
from app.main import app
from app.schemas import ScheduleStartRequest

PASSWORD = "S3cure-Password!"


class _FakeRedis:
    """Enough of Redis for the start route's job registration."""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


@pytest.fixture
def fake_scheduler(monkeypatch):
    """A running APScheduler stand-in on app.state (ASGITransport skips the lifespan)."""
    instance = SimpleNamespace(running=True, add_job=MagicMock(), get_job=lambda _: None)
    monkeypatch.setattr(app.state, "scheduler", instance, raising=False)
    monkeypatch.setattr(scheduler_service, "_new_redis_client", lambda: _FakeRedis())
    return instance


async def _login(
    client: AsyncClient, email: str, role: UserRole = UserRole.OPERATOR
) -> tuple[dict[str, str], uuid.UUID]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await db.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, user_id


async def _seed_agent(user_id: uuid.UUID, *, enabled: bool = True) -> uuid.UUID:
    async with TestSessionLocal() as db:
        account = TradingAccount(
            user_id=user_id,
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
            name="Schedule Audit Agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            enabled=enabled,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _schedule_events() -> list[AuditEvent]:
    async with TestSessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.event_type == AGENT_SCHEDULE_UPDATED)
                    .order_by(AuditEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )


async def _kill_switch_rows() -> int:
    async with TestSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(KillSwitchEvent))).scalar_one()


async def _start(client: AsyncClient, auth, agent_id, interval: int):
    return await client.post(
        "/agent/scheduler/start",
        json={"agent_id": str(agent_id), "interval_minutes": interval},
        headers=auth,
    )


@pytest.mark.asyncio
async def test_a_new_schedule_writes_one_row_with_null_previous_values(
    client: AsyncClient, fake_scheduler
):
    auth, user_id = await _login(client, "schedule-new@example.com")
    agent_id = await _seed_agent(user_id)

    response = await _start(client, auth, agent_id, 5)

    assert response.status_code == 201, response.text
    [event] = await _schedule_events()
    assert event.entity_type == "agent_schedule"
    assert event.entity_id == uuid.UUID(response.json()["schedule"]["id"])
    assert event.actor_id == user_id
    assert event.details == {
        "agent_id": str(agent_id),
        "changes": {
            "interval_minutes": {"from": None, "to": 5},
            "enabled": {"from": None, "to": True},
        },
    }


@pytest.mark.asyncio
async def test_changing_the_interval_writes_the_old_and_new_interval(
    client: AsyncClient, fake_scheduler
):
    auth, user_id = await _login(client, "schedule-change@example.com")
    agent_id = await _seed_agent(user_id)
    first = await _start(client, auth, agent_id, 5)
    assert first.status_code == 201

    response = await _start(client, auth, agent_id, 15)

    assert response.status_code == 201, response.text
    created, changed = await _schedule_events()
    assert changed.entity_id == created.entity_id
    assert changed.actor_id == user_id
    assert changed.details == {
        "agent_id": str(agent_id),
        "changes": {"interval_minutes": {"from": 5, "to": 15}},
    }


@pytest.mark.asyncio
async def test_restarting_with_the_same_interval_while_running_writes_nothing(
    client: AsyncClient, fake_scheduler
):
    auth, user_id = await _login(client, "schedule-same@example.com")
    agent_id = await _seed_agent(user_id)
    assert (await _start(client, auth, agent_id, 5)).status_code == 201

    response = await _start(client, auth, agent_id, 5)

    assert response.status_code == 201, response.text
    assert len(await _schedule_events()) == 1  # only the creation


@pytest.mark.asyncio
async def test_resuming_after_stop_with_the_same_interval_writes_no_schedule_row(
    client: AsyncClient, fake_scheduler
):
    """Stop pauses the user's scheduler, not the schedule row; pause and resume are the
    USER-scope kill_switch_events rows (out of scope for #216)."""
    auth, user_id = await _login(client, "schedule-resume@example.com")
    agent_id = await _seed_agent(user_id)
    started = await _start(client, auth, agent_id, 5)
    schedule_id = started.json()["schedule"]["id"]
    stopped = await client.post(f"/agent/scheduler/{schedule_id}/stop", headers=auth)
    assert stopped.status_code == 200
    switch_rows_before = await _kill_switch_rows()

    response = await _start(client, auth, agent_id, 5)

    assert response.status_code == 201, response.text
    assert len(await _schedule_events()) == 1  # only the creation
    assert await _kill_switch_rows() == switch_rows_before + 1  # the resume


@pytest.mark.asyncio
async def test_re_enabling_a_disabled_schedule_records_enabled(
    client: AsyncClient, fake_scheduler
):
    auth, user_id = await _login(client, "schedule-reenable@example.com")
    agent_id = await _seed_agent(user_id)
    started = await _start(client, auth, agent_id, 5)
    async with TestSessionLocal() as db:  # e.g. disabled by hand in SQL
        schedule = await db.get(AgentSchedule, uuid.UUID(started.json()["schedule"]["id"]))
        assert schedule is not None
        schedule.enabled = False
        await db.commit()

    response = await _start(client, auth, agent_id, 5)

    assert response.status_code == 201, response.text
    assert (await _schedule_events())[-1].details == {
        "agent_id": str(agent_id),
        "changes": {"enabled": {"from": False, "to": True}},
    }


@pytest.mark.asyncio
async def test_a_viewer_gets_403_and_no_row(client: AsyncClient, fake_scheduler):
    auth, user_id = await _login(client, "schedule-viewer@example.com", UserRole.VIEWER)
    agent_id = await _seed_agent(user_id)

    response = await _start(client, auth, agent_id, 5)

    assert response.status_code == 403
    assert await _schedule_events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing agent", "disabled agent", "scheduler not running"])
async def test_a_rejected_start_writes_no_row(
    client: AsyncClient, fake_scheduler, monkeypatch, case: str
):
    auth, user_id = await _login(client, f"schedule-{case.replace(' ', '-')}@example.com")
    agent_id = await _seed_agent(user_id, enabled=case != "disabled agent")
    if case == "missing agent":
        agent_id = uuid.uuid4()
    if case == "scheduler not running":
        monkeypatch.setattr(app.state, "scheduler", SimpleNamespace(running=False))

    response = await _start(client, auth, agent_id, 5)

    expected = {"missing agent": 404, "disabled agent": 409, "scheduler not running": 503}
    assert response.status_code == expected[case]
    assert await _schedule_events() == []
    async with TestSessionLocal() as db:
        assert (await db.execute(select(AgentSchedule))).scalars().all() == []


@pytest.mark.asyncio
async def test_the_concurrent_insert_retry_writes_exactly_one_row():
    """Another request created the schedule first (interval 10): this start's own staged
    row is rolled back with its insert, and the retry writes one row against the winner."""
    user_id = uuid.uuid4()
    agent = SimpleNamespace(id=uuid.uuid4(), enabled=True)
    winner = AgentSchedule(
        id=uuid.uuid4(), user_id=user_id, agent_id=agent.id, interval_minutes=10, enabled=True
    )
    scheduler_instance = SimpleNamespace(running=True, add_job=MagicMock(), get_job=lambda _: None)
    staged: list = []
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: agent),
                SimpleNamespace(scalar_one_or_none=lambda: None),
                SimpleNamespace(scalar_one_or_none=lambda: winner),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [winner])),
            ]
        ),
        scalar=AsyncMock(return_value=True),
        add=MagicMock(side_effect=staged.append),
        commit=AsyncMock(side_effect=[IntegrityError("duplicate schedule", {}, Exception()), None]),
        # A rollback discards everything staged since the last commit.
        rollback=AsyncMock(side_effect=staged.clear),
        refresh=AsyncMock(),
    )

    result = await start_agent_schedule(
        ScheduleStartRequest(agent_id=agent.id, interval_minutes=5),
        request=SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler_instance))
        ),
        db=db,
        current_user=SimpleNamespace(id=user_id, scheduler_enabled=True),
    )

    assert result.schedule.id == winner.id
    db.rollback.assert_awaited_once()
    [event] = [item for item in staged if isinstance(item, AuditEvent)]
    assert event.event_type == AGENT_SCHEDULE_UPDATED
    assert event.entity_id == winner.id
    assert event.actor_id == user_id
    assert event.details == {
        "agent_id": str(agent.id),
        "changes": {"interval_minutes": {"from": 10, "to": 5}},
    }
