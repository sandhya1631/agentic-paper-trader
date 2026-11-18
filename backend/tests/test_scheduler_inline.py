"""Inline scheduler executor (#184): ticks run inside the API process."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from sqlalchemy import select
from test_cycle_recovery import _offline_cycle_deps, _seed_agent, _seed_cycle, _stored

from app.agent import scheduler
from app.core.config import Settings
from app.core.constants import CYCLE_INTERRUPTED
from app.db.enums import CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentCycle


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return None


class _DownRedis:
    """A configured Redis that is unreachable."""

    def __getattr__(self, _name):
        def fail(*_args, **_kwargs):
            raise ConnectionError("redis down")

        return fail

    def close(self):
        pass


class _SetNxRedis:
    def __init__(self):
        self.keys = set()

    def set(self, key, _value, *, nx, ex):
        if key in self.keys:
            return None
        self.keys.add(key)
        return True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clean_inline_state(monkeypatch):
    monkeypatch.setattr(scheduler, "_claimed_inline_slots", {})
    monkeypatch.setattr(scheduler, "_tick_history", scheduler.defaultdict(
        lambda: scheduler.deque(maxlen=100)
    ))
    monkeypatch.setattr(scheduler, "_schedule_tick_statuses", {})
    monkeypatch.setattr(scheduler, "_last_tick_status", None)
    # Freeze only the scheduler's clock: patching the global time.time would also
    # freeze SQLAlchemy's asyncpg schema-cache timestamps, so a DB-backed test here
    # would reuse enum type OIDs that an earlier test's table reset dropped.
    monkeypatch.setattr(scheduler, "time", SimpleNamespace(time=lambda: 1_800))


def _inline(monkeypatch, redis_url=""):
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: Settings(scheduler_executor="inline", redis_url=redis_url),
    )


def _no_redis_allowed():
    raise AssertionError("Redis must not be used when REDIS_URL is empty")


@pytest.mark.asyncio
async def test_inline_dispatch_runs_once_per_interval_slot(monkeypatch):
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)
    run_tick = AsyncMock()
    monkeypatch.setattr(scheduler, "run_scheduled_cycles", run_tick)
    celery_task = MagicMock()
    monkeypatch.setattr(scheduler, "run_scheduled_cycles_task", celery_task)

    await scheduler.dispatch_scheduled_cycles(5)
    await scheduler.dispatch_scheduled_cycles(5)  # same 5-minute slot

    run_tick.assert_awaited_once()
    celery_task.delay.assert_not_called()

    monkeypatch.setattr(scheduler.time, "time", lambda: 1_800 + 300)  # next slot
    await scheduler.dispatch_scheduled_cycles(5)
    assert run_tick.await_count == 2


@pytest.mark.asyncio
async def test_inline_agent_schedule_runs_that_schedule(monkeypatch):
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)
    run_schedule = AsyncMock()
    monkeypatch.setattr(scheduler, "run_agent_schedule", run_schedule)
    schedule_id = uuid.uuid4()

    await scheduler.dispatch_agent_schedule(schedule_id, 1)
    await scheduler.dispatch_agent_schedule(schedule_id, 1)

    run_schedule.assert_awaited_once_with(schedule_id)


@pytest.mark.asyncio
async def test_inline_dispatch_uses_redis_set_nx_when_configured(monkeypatch):
    _inline(monkeypatch, redis_url="redis://localhost:6379/0")
    redis_client = _SetNxRedis()
    redis_client.keys.add("agent-scheduler:global:6")  # another instance took this slot
    monkeypatch.setattr(scheduler, "_new_redis_client", lambda: redis_client)
    run_tick = AsyncMock()
    monkeypatch.setattr(scheduler, "run_scheduled_cycles", run_tick)

    await scheduler.dispatch_scheduled_cycles(5)

    run_tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_tick_with_kill_switch_skips_without_creating_a_cycle(monkeypatch):
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)
    monkeypatch.setattr(
        scheduler, "AsyncSessionLocal", lambda: _SessionContext(SimpleNamespace())
    )
    monkeypatch.setattr(scheduler, "is_kill_switch_enabled", AsyncMock(return_value=True))
    run_cycles = AsyncMock()
    monkeypatch.setattr(scheduler, "run_watchlist_cycles", run_cycles)

    await scheduler.dispatch_scheduled_cycles(5)

    run_cycles.assert_not_awaited()
    [tick] = scheduler.get_tick_history()
    assert (tick.status, tick.reason) == ("SKIPPED", "kill_switch_enabled")


@pytest.mark.asyncio
async def test_inline_tick_runs_when_configured_redis_is_down(monkeypatch):
    _inline(monkeypatch, redis_url="redis://localhost:6379/0")
    monkeypatch.setattr(scheduler, "_new_redis_client", lambda: _DownRedis())
    agent_id = uuid.uuid4()
    sessions = iter(
        [
            SimpleNamespace(),  # kill-switch check
            SimpleNamespace(  # enabled agents query
                execute=AsyncMock(
                    return_value=SimpleNamespace(all=lambda: [(agent_id, UserRole.OPERATOR)])
                )
            ),
            SimpleNamespace(  # per-agent re-check
                execute=AsyncMock(
                    return_value=SimpleNamespace(scalar_one_or_none=lambda: agent_id)
                )
            ),
        ]
    )
    monkeypatch.setattr(
        scheduler, "AsyncSessionLocal", lambda: _SessionContext(next(sessions))
    )
    monkeypatch.setattr(scheduler, "is_kill_switch_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        scheduler,
        "_fetch_market_clock",
        AsyncMock(return_value=scheduler.MarketClock(is_open=True)),
    )
    run_cycles = AsyncMock()
    monkeypatch.setattr(scheduler, "run_watchlist_cycles", run_cycles)

    await scheduler.dispatch_scheduled_cycles(5)

    run_cycles.assert_awaited_once()
    # History endpoints don't error; they fall back to this process's memory.
    [tick] = scheduler.get_tick_history()
    assert tick.status == "COMPLETED"
    assert scheduler.get_last_tick_status() == tick


def test_history_without_redis_is_empty_not_an_error(monkeypatch):
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)

    assert scheduler.get_tick_history() == []
    assert scheduler.get_tick_history(uuid.uuid4()) == []
    assert scheduler.get_last_tick_status() is None


@pytest.mark.asyncio
async def test_inline_tick_error_does_not_escape_the_job(monkeypatch):
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)
    monkeypatch.setattr(
        scheduler, "run_scheduled_cycles", AsyncMock(side_effect=RuntimeError("boom"))
    )

    await scheduler.dispatch_scheduled_cycles(5)  # must not raise


@pytest.mark.asyncio
async def test_celery_mode_still_enqueues_through_dispatch_once(monkeypatch):
    monkeypatch.setattr(scheduler, "get_settings", lambda: Settings())
    dispatch_once = MagicMock(return_value=True)
    monkeypatch.setattr(scheduler, "_dispatch_once", dispatch_once)
    task = MagicMock()
    monkeypatch.setattr(scheduler, "run_agent_schedule_task", task)
    run_inline = AsyncMock()
    monkeypatch.setattr(scheduler, "run_agent_schedule", run_inline)
    schedule_id = uuid.uuid4()

    await scheduler.dispatch_agent_schedule(schedule_id, 5)

    dispatch_once.assert_called_once_with(
        f"schedule-{schedule_id}", 5, task.delay, str(schedule_id)
    )
    run_inline.assert_not_awaited()


def test_scheduler_executor_defaults_to_celery():
    assert Settings().scheduler_executor == "celery"


@pytest.mark.asyncio
async def test_inline_tick_recovers_an_orphaned_scheduled_cycle(db_session, monkeypatch):
    """#185: with SCHEDULER_EXECUTOR=inline a redeploy mid-tick orphans a SCHEDULED
    cycle (which the cancel route refuses). The next inline tick's gate recovers it
    and the sweep runs, instead of the agent being skipped as busy forever."""
    _inline(monkeypatch)
    monkeypatch.setattr(scheduler, "_new_redis_client", _no_redis_allowed)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(scheduler, "is_kill_switch_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        scheduler,
        "_fetch_market_clock",
        AsyncMock(return_value=scheduler.MarketClock(is_open=True)),
    )
    _offline_cycle_deps(monkeypatch)
    agent_id = await _seed_agent()
    orphan_id = await _seed_cycle(
        agent_id, CycleStatus.REASONING, trigger=CycleTrigger.SCHEDULED
    )

    await scheduler.dispatch_scheduled_cycles(5)

    [tick] = scheduler.get_tick_history()
    assert (tick.status, tick.overlapping_agents) == ("COMPLETED", 0)
    orphan = await _stored(orphan_id)
    assert (orphan.status, orphan.failure_code) == (CycleStatus.FAILED, CYCLE_INTERRUPTED)
    async with TestSessionLocal() as db:
        swept = (
            await db.execute(
                select(AgentCycle).where(
                    AgentCycle.agent_id == agent_id, AgentCycle.id != orphan_id
                )
            )
        ).scalars().all()
    assert [(c.trigger, c.status) for c in swept] == [
        (CycleTrigger.SCHEDULED, CycleStatus.HOLD)
    ]
