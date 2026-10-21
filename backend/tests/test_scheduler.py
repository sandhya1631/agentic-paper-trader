import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app import main as main_module
from app.agent import scheduler
from app.agent.orchestrator import CycleOverlapError
from app.agent.router import (
    get_scheduler_logs,
    get_scheduler_status,
    start_agent_schedule,
    stop_agent_schedule,
)
from app.core.config import Settings
from app.db.enums import (
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    OrderSide,
)
from app.db.models import AgentSchedule, BrokerOrder
from app.risk.market_hours import MarketClock
from app.schemas import SchedulerTickStatus, ScheduleStartRequest


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


@pytest.mark.asyncio
async def test_lifespan_starts_scheduler_before_yield_and_stops_it(monkeypatch):
    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_):
            return None

    connection = SimpleNamespace(run_sync=AsyncMock())
    engine = SimpleNamespace(begin=lambda: AsyncContext(connection))
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        )
    )
    scheduler_instance = SimpleNamespace(
        start=MagicMock(),
        shutdown=MagicMock(),
    )
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(
        main_module,
        "ensure_agent_cycle_prompt_columns",
        AsyncMock(),
    )
    monkeypatch.setattr(
        main_module,
        "AsyncSessionLocal",
        lambda: AsyncContext(db),
    )
    monkeypatch.setattr(
        main_module,
        "create_scheduler",
        MagicMock(return_value=scheduler_instance),
    )

    async with main_module.lifespan(main_module.app):
        assert main_module.app.state.scheduler is scheduler_instance
        scheduler_instance.start.assert_called_once()

    scheduler_instance.shutdown.assert_called_once_with(wait=False)
    assert main_module.app.state.scheduler is None


@pytest.fixture
def scheduled_tick(monkeypatch):
    enabled_agent_ids = [uuid.uuid4(), uuid.uuid4()]
    query_session = _make_session(enabled_agent_ids)
    cycle_sessions = [
        SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        for _ in enabled_agent_ids
    ]
    sessions = iter([_SessionContext(None), query_session, *cycle_sessions])
    monkeypatch.setattr(
        scheduler,
        "AsyncSessionLocal",
        lambda: _SessionContext(next(sessions)),
    )
    monkeypatch.setattr(scheduler, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        scheduler, "is_kill_switch_enabled", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(scheduler, "_fetch_market_clock", AsyncMock(return_value=_open_market()))
    run_cycle = AsyncMock()
    monkeypatch.setattr(scheduler, "run_agent_cycle", run_cycle)
    return enabled_agent_ids, query_session, run_cycle


@pytest.mark.asyncio
async def test_scheduled_tick_runs_each_enabled_agent(scheduled_tick):
    agent_ids, query_session, run_cycle = scheduled_tick

    result = await scheduler.run_scheduled_cycles()

    assert result.status == "COMPLETED"
    assert result.enabled_agents == len(agent_ids)
    assert result.attempted_agents == len(agent_ids)
    assert result.successful_agents == len(agent_ids)
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
    assert scheduler.get_tick_history(limit=1)[0] == result


@pytest.mark.asyncio
async def test_scheduled_tick_history_records_market_closed_skip(scheduled_tick, monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "_fetch_market_clock",
        AsyncMock(return_value=MarketClock(is_open=False)),
    )

    result = await scheduler.run_scheduled_cycles()

    assert result.status == "SKIPPED"
    assert scheduler.get_tick_history(limit=1)[0] == result


@pytest.mark.asyncio
async def test_scheduler_logs_returns_recent_global_and_owned_schedule_ticks(monkeypatch):
    schedule_id = uuid.uuid4()
    tick = SchedulerTickStatus(
        status="SKIPPED",
        reason="market_closed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        enabled_agents=1,
        attempted_agents=0,
        successful_agents=0,
        failed_agents=0,
        overlapping_agents=0,
    )
    monkeypatch.setattr(
        scheduler,
        "get_tick_history",
        lambda schedule_id=None, *, limit=50: [tick],
    )

    result = await get_scheduler_logs(
        limit=10,
        db=SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [schedule_id])
                )
            )
        ),
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert len(result.logs) == 2
    assert result.logs[0].schedule_id is None
    assert result.logs[1].schedule_id == schedule_id
    assert all(entry.tick.reason == "market_closed" for entry in result.logs)


@pytest.mark.asyncio
async def test_scheduled_tick_skips_when_market_is_closed(scheduled_tick, monkeypatch):
    _, _, run_cycle = scheduled_tick
    monkeypatch.setattr(
        scheduler,
        "_fetch_market_clock",
        AsyncMock(return_value=MarketClock(is_open=False)),
    )

    result = await scheduler.run_scheduled_cycles()

    assert result.status == "SKIPPED"
    assert result.reason == "market_closed"
    run_cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_tick_skips_when_kill_switch_is_enabled(
    scheduled_tick, monkeypatch, caplog
):
    _, _, run_cycle = scheduled_tick
    monkeypatch.setattr(
        scheduler, "is_kill_switch_enabled", AsyncMock(return_value=True)
    )

    with caplog.at_level(logging.WARNING):
        result = await scheduler.run_scheduled_cycles()

    assert result.status == "SKIPPED"
    assert result.reason == "kill_switch_enabled"
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


@pytest.mark.asyncio
async def test_start_schedule_persists_interval_and_registers_job():
    schedule_id = uuid.uuid4()
    user_id = uuid.uuid4()
    agent = SimpleNamespace(id=uuid.uuid4(), enabled=True)
    scheduler_instance = SimpleNamespace(
        running=True,
        add_job=MagicMock(),
        get_job=lambda _: None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler_instance))
    )

    def assign_schedule_id(schedule):
        schedule.id = schedule_id

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: agent),
                SimpleNamespace(scalar_one_or_none=lambda: None),
            ]
        ),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=assign_schedule_id),
    )
    result = await start_agent_schedule(
        ScheduleStartRequest(agent_id=agent.id, interval_minutes=5),
        request=request,
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )

    assert result.schedule.id == schedule_id
    assert result.schedule.interval_minutes == 5
    assert result.schedule.enabled is True
    scheduler_instance.add_job.assert_called_once()
    assert scheduler_instance.add_job.call_args.kwargs["minutes"] == 5


@pytest.mark.asyncio
async def test_start_schedule_recovers_from_concurrent_insert():
    schedule_id = uuid.uuid4()
    user_id = uuid.uuid4()
    agent = SimpleNamespace(id=uuid.uuid4(), enabled=True)
    winner_schedule = AgentSchedule(
        id=schedule_id,
        user_id=user_id,
        agent_id=agent.id,
        interval_minutes=10,
        enabled=True,
    )
    scheduler_instance = SimpleNamespace(
        running=True,
        add_job=MagicMock(),
        get_job=lambda _: None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler_instance))
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: agent),
                SimpleNamespace(scalar_one_or_none=lambda: None),
                SimpleNamespace(scalar_one_or_none=lambda: winner_schedule),
            ]
        ),
        add=MagicMock(),
        commit=AsyncMock(
            side_effect=[
                IntegrityError("duplicate schedule", {}, Exception()),
                None,
            ]
        ),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await start_agent_schedule(
        ScheduleStartRequest(agent_id=agent.id, interval_minutes=5),
        request=request,
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )

    assert result.schedule.id == schedule_id
    assert result.schedule.interval_minutes == 5
    assert winner_schedule.enabled is True
    db.rollback.assert_awaited_once()
    assert db.commit.await_count == 2
    scheduler_instance.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_stop_schedule_disables_persisted_schedule():
    schedule = AgentSchedule(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        interval_minutes=5,
        enabled=True,
    )
    scheduler_instance = SimpleNamespace(
        get_job=lambda _: SimpleNamespace(
            id="scheduled-job",
            next_run_time=None,
        ),
        remove_job=MagicMock(),
    )
    result = await stop_agent_schedule(
        schedule.id,
        request=SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler_instance))
        ),
        db=SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(scalar_one_or_none=lambda: schedule)
            ),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        ),
        current_user=SimpleNamespace(id=schedule.user_id),
    )

    assert schedule.enabled is False
    assert result.enabled is False
    scheduler_instance.remove_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_status_reports_scheduler_and_order_status():
    user_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    next_run_time = datetime.now(UTC)
    schedule = AgentSchedule(
        id=schedule_id,
        user_id=user_id,
        agent_id=agent_id,
        interval_minutes=5,
        enabled=True,
    )
    cycle = SimpleNamespace(
        id=cycle_id,
        agent_id=agent_id,
        status=CycleStatus.COMPLETED,
        started_at=next_run_time,
        completed_at=next_run_time,
        proposal_json={"action": "BUY", "symbol": "AAPL", "quantity": 1},
        approval=None,
        snapshot_json={},
    )
    order = BrokerOrder(
        id=uuid.uuid4(),
        decision_id=cycle_id,
        client_order_id="test-order-123",
        status=BrokerOrderStatus.FILLED,
        symbol="AAPL",
        side=OrderSide.BUY,
        requested_quantity=1,
        filled_quantity=1,
        average_fill_price=100,
    )
    result = await get_scheduler_status(
        request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    scheduler=SimpleNamespace(
                        running=True,
                        get_job=lambda _: SimpleNamespace(next_run_time=next_run_time),
                    )
                )
            )
        ),
        db=SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    SimpleNamespace(
                        scalars=lambda: SimpleNamespace(all=lambda: [schedule])
                    ),
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [cycle])),
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [order])),
                ]
            )
        ),
        current_user=SimpleNamespace(id=user_id),
    )

    assert result.running is True
    assert result.next_run_time == next_run_time
    assert result.recent_cycles[0].cycle_id == cycle_id
    assert result.recent_cycles[0].execution_status == "FILLED"
    assert result.recent_cycles[0].orders[0].status == BrokerOrderStatus.FILLED
    assert "paper-order submission" in result.trade_execution_note


@pytest.mark.asyncio
async def test_scheduler_status_reports_when_proposal_has_no_order(monkeypatch):
    cycle = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status=CycleStatus.COMPLETED,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        proposal_json={"action": "BUY", "symbol": "AAPL", "quantity": 1},
        approval=None,
        snapshot_json={},
    )
    monkeypatch.setattr(
        "app.agent.router.get_settings",
        lambda: Settings(scheduler_enabled=False),
    )
    result = await get_scheduler_status(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=None))),
        db=SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [cycle])),
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
                ]
            )
        ),
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.enabled is False
    assert result.running is False
    assert result.recent_cycles[0].execution_status == "NO_ORDER"
    assert result.recent_cycles[0].orders == []
