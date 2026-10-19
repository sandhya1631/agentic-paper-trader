import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import scheduler
from app.agent.orchestrator import CycleOverlapError
from app.core.config import Settings
from app.db.enums import CycleTrigger
from app.risk.market_hours import MarketClock


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return None


def _make_session(agent_ids):
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: agent_ids)
    )
    return SimpleNamespace(
        execute=AsyncMock(return_value=result),
    )


def _open_market():
    return MarketClock(is_open=True)


@pytest.fixture
def scheduled_tick(monkeypatch):
    enabled_agent_ids = [uuid.uuid4(), uuid.uuid4()]
    query_session = _make_session(enabled_agent_ids)
    cycle_sessions = [_make_session([]) for _ in enabled_agent_ids]
    sessions = iter([query_session, *cycle_sessions])
    monkeypatch.setattr(
        scheduler,
        "AsyncSessionLocal",
        lambda: _SessionContext(next(sessions)),
    )
    monkeypatch.setattr(scheduler, "get_settings", lambda: Settings())
    monkeypatch.setattr(scheduler, "_fetch_market_clock", AsyncMock(return_value=_open_market()))
    run_cycle = AsyncMock()
    monkeypatch.setattr(scheduler, "run_agent_cycle", run_cycle)
    return enabled_agent_ids, query_session, run_cycle


@pytest.mark.asyncio
async def test_scheduled_tick_runs_each_enabled_agent(scheduled_tick):
    agent_ids, query_session, run_cycle = scheduled_tick

    await scheduler.run_scheduled_cycles()

    assert run_cycle.await_count == len(agent_ids)
    assert [call.args[0] for call in run_cycle.await_args_list] == [
        agent_id for agent_id in agent_ids
    ]
    assert all(
        call.kwargs["trigger"] is CycleTrigger.SCHEDULED
        for call in run_cycle.await_args_list
    )
    assert "agent_configs.enabled IS true" in str(
        query_session.execute.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_scheduled_tick_skips_when_market_is_closed(scheduled_tick, monkeypatch):
    _, _, run_cycle = scheduled_tick
    monkeypatch.setattr(
        scheduler,
        "_fetch_market_clock",
        AsyncMock(return_value=MarketClock(is_open=False)),
    )

    await scheduler.run_scheduled_cycles()

    run_cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_tick_skips_when_kill_switch_is_enabled(
    scheduled_tick, monkeypatch, caplog
):
    _, _, run_cycle = scheduled_tick
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: Settings(kill_switch_enabled=True),
    )

    with caplog.at_level(logging.WARNING):
        await scheduler.run_scheduled_cycles()

    run_cycle.assert_not_awaited()
    assert "kill switch is enabled" in caplog.text


@pytest.mark.asyncio
async def test_overlap_does_not_prevent_later_agent_cycle(scheduled_tick):
    agents, _, run_cycle = scheduled_tick
    run_cycle.side_effect = [
        CycleOverlapError("already running"),
        None,
    ]

    await scheduler.run_scheduled_cycles()

    assert run_cycle.await_count == len(agents)


@pytest.mark.asyncio
async def test_cycle_error_does_not_prevent_later_agent_cycle(scheduled_tick):
    agent_ids, _, run_cycle = scheduled_tick
    run_cycle.side_effect = [RuntimeError("cycle failed"), None]

    await scheduler.run_scheduled_cycles()

    assert run_cycle.await_count == len(agent_ids)


def test_scheduler_uses_configured_interval():
    scheduler_instance = scheduler.create_scheduler(Settings(scheduler_interval_minutes=15))

    job = scheduler_instance.get_job("scheduled-agent-cycles")

    assert job is not None
    assert job.trigger.interval.total_seconds() == 15 * 60
