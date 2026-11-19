"""A real concurrent schedule start uses the IntegrityError retry path (#220).

The route's own SELECT can find no existing schedule, then a second, truly concurrent
request commits the same (user_id, agent_id) row before this request's own flush, so
that flush hits a genuine unique violation. This fires a second asyncio Task (a real,
pre-connected competing INSERT, not a stub) from inside `AgentConfig.add`'s synchronous
hook right when the route adds its own pending schedule, so it races the route's own
flush on real timing, the same way two concurrent HTTP requests would — wrapping the
route's own awaited calls (its `flush`/`execute`) to run extra I/O first was tried and
rejected: it corrupts the async session's internal greenlet bridge on this stack
(reproducible even with zero shared state in the injected await), which is an
environment-specific SQLAlchemy/asyncio quirk, not something to route around in
application code. Because winning a real race is inherently timing-dependent, the
whole attempt (fresh tables, fresh agent, fresh race) retries a bounded number of times
until the competitor actually wins once — then asserts on that result.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncpg
import pytest
from conftest import TEST_DATABASE_URL, TestSessionLocal, test_engine
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.agent.router import start_agent_schedule
from app.db.base import Base
from app.db.enums import ApprovalMode, UserRole
from app.db.models import AgentConfig, AgentSchedule, PolicySet, TradingAccount, User
from app.schemas import ScheduleStartRequest

_ASYNCPG_DSN = make_url(TEST_DATABASE_URL).set(drivername="postgresql").render_as_string(
    hide_password=False
)
_MAX_ATTEMPTS = 20


async def _reset_tables() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed_agent() -> tuple[uuid.UUID, uuid.UUID]:
    async with TestSessionLocal() as db:
        user = User(
            email=f"schedule-race-{uuid.uuid4()}@example.com",
            password_hash="x",
            role=UserRole.OPERATOR,
            scheduler_enabled=False,
        )
        db.add(user)
        await db.flush()
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
            name="Concurrency Test Agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=ApprovalMode.REQUIRED,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return user.id, agent.id


async def _one_attempt() -> bool | None:
    """One race attempt. Returns True if the competitor won (the real scenario this
    issue is about), False if the route's own insert won instead (still a valid run,
    just not the one that exercises the retry path), or raises if the route answered
    with anything other than a clean 201 with one correctly-updated row.
    """
    await _reset_tables()
    user_id, agent_id = await _seed_agent()

    async with TestSessionLocal() as db:
        user = await db.get(User, user_id)

        competitor_conn = await asyncpg.connect(_ASYNCPG_DSN)
        competitor_stmt = await competitor_conn.prepare(
            "INSERT INTO agent_schedules "
            "(id, user_id, agent_id, interval_minutes, enabled, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, true, now(), now())"
        )
        competitor_won = False

        async def competitor() -> None:
            nonlocal competitor_won
            try:
                await competitor_stmt.fetch(uuid.uuid4(), user_id, agent_id, 10)
                competitor_won = True
            except asyncpg.UniqueViolationError:
                pass
            finally:
                await competitor_conn.close()

        real_add = db.add
        fired = False
        competitor_task = None

        def racing_add(instance):
            nonlocal fired, competitor_task
            if isinstance(instance, AgentSchedule) and not fired:
                fired = True
                competitor_task = asyncio.get_running_loop().create_task(competitor())
            return real_add(instance)

        db.add = racing_add

        scheduler_instance = SimpleNamespace(
            running=True, add_job=MagicMock(), get_job=lambda _: None
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler_instance))
        )

        result = await start_agent_schedule(
            ScheduleStartRequest(agent_id=agent_id, interval_minutes=5),
            request=request,
            db=db,
            current_user=user,
        )
        if competitor_task is not None:
            await competitor_task

        assert result.schedule.interval_minutes == 5
        rows = (
            (await db.execute(select(AgentSchedule).where(AgentSchedule.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].interval_minutes == 5
        assert rows[0].enabled is True
        return competitor_won


@pytest.mark.asyncio
async def test_concurrent_schedule_start_answers_201_via_the_retry_path():
    """#220: whichever side's INSERT reaches Postgres first, the start route answers
    201 with exactly one schedule row carrying the requested interval — never a 500,
    and never two rows. Retries until a run where the competitor actually won is
    captured, since that is the specific scenario #220 is about."""
    for _ in range(_MAX_ATTEMPTS):
        if await _one_attempt():
            return
    pytest.fail(
        f"the competitor never won the race in {_MAX_ATTEMPTS} attempts; "
        "the retry path (the actual point of #220) was never exercised"
    )
