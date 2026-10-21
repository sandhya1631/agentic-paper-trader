"""Periodic entry point for enabled agent cycles."""

import logging
from collections import defaultdict, deque
from datetime import UTC, datetime
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.broker.alpaca import AlpacaClient
from app.core.config import Settings, get_settings
from app.db.enums import ApprovalStatus, CycleStatus, CycleTrigger
from app.db.models import (
    AgentConfig,
    AgentCycle,
    AgentSchedule,
    CycleApproval,
    TradingAccount,
    User,
    utcnow,
)
from app.db.session import AsyncSessionLocal
from app.risk.kill_switch import is_kill_switch_enabled
from app.risk.market_hours import MarketClock, check_market_open, fetch_market_clock_for
from app.schemas import SchedulerTickStatus

logger = logging.getLogger(__name__)
_last_tick_status: SchedulerTickStatus | None = None
_schedule_tick_statuses: dict[UUID, SchedulerTickStatus] = {}
_tick_history: dict[UUID | None, deque[SchedulerTickStatus]] = defaultdict(
    lambda: deque(maxlen=100)
)


async def _expire_pending_approvals(db: AsyncSession, agent_id: UUID) -> None:
    now = utcnow()
    result = await db.execute(
        select(CycleApproval, AgentCycle)
        .join(AgentCycle, CycleApproval.cycle_id == AgentCycle.id)
        .where(
            AgentCycle.agent_id == agent_id,
            CycleApproval.status == ApprovalStatus.PENDING,
            CycleApproval.expires_at <= now,
        )
        .with_for_update()
    )
    expired = result.all()
    for approval, cycle in expired:
        approval.status = ApprovalStatus.EXPIRED
        approval.acted_at = now
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "APPROVAL_EXPIRED"
        cycle.completed_at = now
        cycle.snapshot_json = {
            **(cycle.snapshot_json or {}),
            "execution_status": "APPROVAL_EXPIRED",
        }
    if expired:
        await db.commit()


def get_last_tick_status(schedule_id: UUID | None = None) -> SchedulerTickStatus | None:
    if schedule_id is not None:
        return _schedule_tick_statuses.get(schedule_id)
    return _last_tick_status


def get_tick_history(
    schedule_id: UUID | None = None, *, limit: int = 50
) -> list[SchedulerTickStatus]:
    """Return the most recent in-process tick summaries, newest first."""
    return list(_tick_history[schedule_id])[-limit:][::-1]


async def _fetch_market_clock(settings: Settings) -> MarketClock:
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        raise ValueError("Alpaca API credentials are required to check market hours")

    async with AlpacaClient(
        api_key=settings.alpaca_api_key,
        api_secret=settings.alpaca_api_secret,
        base_url=settings.alpaca_paper_base_url,
    ) as alpaca:
        return await fetch_market_clock_for(alpaca)


async def run_scheduled_cycles(
    *, agent_id_filter: UUID | None = None, schedule_id: UUID | None = None
) -> SchedulerTickStatus:
    """Run one scheduled tick and retain an in-process summary for monitoring."""
    global _last_tick_status

    started_at = datetime.now(UTC)
    enabled_agents = 0
    attempted_agents = 0
    successful_agents = 0
    failed_agents = 0
    overlapping_agents = 0

    def finish(status: str, reason: str | None = None) -> SchedulerTickStatus:
        global _last_tick_status
        _last_tick_status = SchedulerTickStatus(
            status=status,
            reason=reason,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            enabled_agents=enabled_agents,
            attempted_agents=attempted_agents,
            successful_agents=successful_agents,
            failed_agents=failed_agents,
            overlapping_agents=overlapping_agents,
        )
        if schedule_id is not None:
            _schedule_tick_statuses[schedule_id] = _last_tick_status
        _tick_history[schedule_id].append(_last_tick_status)
        logger.info(
            "Scheduled tick finished: schedule_id=%s status=%s reason=%s "
            "enabled_agents=%d attempted_agents=%d successful_agents=%d "
            "failed_agents=%d overlapping_agents=%d",
            schedule_id,
            status,
            reason,
            enabled_agents,
            attempted_agents,
            successful_agents,
            failed_agents,
            overlapping_agents,
        )
        return _last_tick_status

    settings = get_settings()
    try:
        async with AsyncSessionLocal() as db:
            kill_switch_enabled = await is_kill_switch_enabled(db)
    except Exception:
        logger.exception("Scheduled agent tick failed while checking kill switch")
        return finish("FAILED", "kill_switch_check_failed")
    if kill_switch_enabled:
        logger.warning("Scheduled agent tick skipped: kill switch is enabled")
        return finish("SKIPPED", "kill_switch_enabled")

    try:
        async with AsyncSessionLocal() as db:
            query = (
                select(AgentConfig.id)
                .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
                .join(User, TradingAccount.user_id == User.id)
                .where(
                    AgentConfig.enabled.is_(True),
                    User.scheduler_enabled.is_(True),
                )
            )
            if agent_id_filter is not None:
                query = query.where(AgentConfig.id == agent_id_filter)
            else:
                scheduled_agent_ids = select(AgentSchedule.agent_id).where(
                    AgentSchedule.enabled.is_(True)
                )
                query = query.where(AgentConfig.id.not_in(scheduled_agent_ids))
            result = await db.execute(query)
            agent_ids = result.scalars().all()
    except Exception:
        logger.exception("Scheduled agent tick failed while loading enabled agents")
        return finish("FAILED", "agent_query_failed")

    enabled_agents = len(agent_ids)
    if not agent_ids:
        return finish("SKIPPED", "no_enabled_agents")

    try:
        clock = await _fetch_market_clock(settings)
    except Exception:
        logger.exception("Scheduled agent tick skipped: market clock check failed")
        return finish("FAILED", "market_clock_check_failed")

    market_hours = check_market_open(clock)
    if not market_hours.allowed:
        logger.info("Scheduled agent tick skipped: %s", market_hours.reason)
        return finish("SKIPPED", "market_closed")

    for agent_id in agent_ids:
        try:
            async with AsyncSessionLocal() as db:
                enabled_result = await db.execute(
                    select(AgentConfig.id)
                    .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
                    .join(User, TradingAccount.user_id == User.id)
                    .where(
                        AgentConfig.id == agent_id,
                        AgentConfig.enabled.is_(True),
                        User.scheduler_enabled.is_(True),
                    )
                )
                if enabled_result.scalar_one_or_none() is None:
                    continue
                attempted_agents += 1
                await _expire_pending_approvals(db, agent_id)
                await run_agent_cycle(
                    agent_id,
                    db,
                    trigger=CycleTrigger.SCHEDULED,
                )
            successful_agents += 1
        except CycleOverlapError:
            overlapping_agents += 1
            logger.info(
                "Scheduled cycle skipped for agent %s: cycle already in progress",
                agent_id,
            )
        except Exception:
            failed_agents += 1
            logger.exception("Scheduled cycle failed for agent %s", agent_id)

    if failed_agents and successful_agents:
        return finish("PARTIAL", "some_agent_cycles_failed")
    if failed_agents:
        return finish("FAILED", "agent_cycle_failed")
    if overlapping_agents and not successful_agents:
        return finish("SKIPPED", "all_agents_already_running")
    if overlapping_agents:
        return finish("PARTIAL", "some_agents_already_running")
    return finish("COMPLETED")


async def run_agent_schedule(schedule_id: UUID) -> SchedulerTickStatus | None:
    """Run one tick for a persisted schedule, if the schedule remains active."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentSchedule.agent_id)
            .join(AgentConfig, AgentConfig.id == AgentSchedule.agent_id)
            .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
            .join(User, TradingAccount.user_id == User.id)
            .where(
                AgentSchedule.id == schedule_id,
                AgentSchedule.enabled.is_(True),
                AgentConfig.enabled.is_(True),
                User.scheduler_enabled.is_(True),
            )
        )
        agent_id = result.scalar_one_or_none()
    if agent_id is None:
        return None
    return await run_scheduled_cycles(
        agent_id_filter=agent_id,
        schedule_id=schedule_id,
    )


def add_agent_schedule_job(
    scheduler: AsyncIOScheduler,
    schedule_id: UUID,
    interval_minutes: int,
) -> None:
    scheduler.add_job(
        run_agent_schedule,
        trigger="interval",
        minutes=interval_minutes,
        args=[schedule_id],
        id=f"agent-schedule-{schedule_id}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def create_scheduler(
    settings: Settings | None = None, *, include_global_job: bool = True
) -> AsyncIOScheduler:
    """Create an interval scheduler; application lifespan owns its start/stop."""
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler()
    if include_global_job:
        scheduler.add_job(
            run_scheduled_cycles,
            trigger="interval",
            minutes=settings.scheduler_interval_minutes,
            id="scheduled-agent-cycles",
            max_instances=1,
            coalesce=True,
        )
    return scheduler
