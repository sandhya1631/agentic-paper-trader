"""Tests for the Celery task wrappers (Story 2.3.1.1 / Issue #112)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent import scheduler as scheduler_module
from app.agent import tasks
from app.schemas import SchedulerTickStatus


def _tick(status: str = "COMPLETED") -> SchedulerTickStatus:
    now = datetime.now(UTC)
    return SchedulerTickStatus(
        status=status,
        started_at=now,
        completed_at=now,
        enabled_agents=2,
        attempted_agents=2,
        successful_agents=2,
        failed_agents=0,
        overlapping_agents=0,
    )


# --- _run_async ---


def test_run_async_returns_the_coroutine_result():
    async def coro():
        return "ok"

    assert tasks._run_async(coro()) == "ok"


def test_run_async_disposes_the_engine_after_success(monkeypatch):
    dispose = AsyncMock()
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=dispose))

    async def coro():
        return 1

    tasks._run_async(coro())

    dispose.assert_awaited_once()


def test_run_async_disposes_the_engine_even_when_the_coroutine_raises(monkeypatch):
    dispose = AsyncMock()
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=dispose))

    async def coro():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        tasks._run_async(coro())

    dispose.assert_awaited_once()


# --- run_scheduled_cycles_task ---


def test_run_scheduled_cycles_task_returns_serialized_tick(monkeypatch):
    tick = _tick()
    monkeypatch.setattr(scheduler_module, "run_scheduled_cycles", AsyncMock(return_value=tick))
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=AsyncMock()))

    result = tasks.run_scheduled_cycles_task()

    assert result == tick.model_dump(mode="json")


# --- run_agent_schedule_task ---


def test_run_agent_schedule_task_returns_serialized_tick_when_run(monkeypatch):
    tick = _tick()
    schedule_id = uuid4()
    run_agent_schedule = AsyncMock(return_value=tick)
    monkeypatch.setattr(scheduler_module, "run_agent_schedule", run_agent_schedule)
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=AsyncMock()))

    result = tasks.run_agent_schedule_task(str(schedule_id))

    assert result == tick.model_dump(mode="json")
    run_agent_schedule.assert_awaited_once_with(schedule_id)


def test_run_agent_schedule_task_returns_none_when_schedule_did_not_run(monkeypatch):
    monkeypatch.setattr(
        scheduler_module, "run_agent_schedule", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=AsyncMock()))

    result = tasks.run_agent_schedule_task(str(uuid4()))

    assert result is None
