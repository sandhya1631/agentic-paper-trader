"""Periodic entry point for enabled agent cycles."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.broker.alpaca import AlpacaClient
from app.core.config import Settings, get_settings
from app.db.enums import CycleTrigger
from app.db.models import AgentConfig
from app.db.session import AsyncSessionLocal
from app.risk.market_hours import MarketClock, check_market_open, fetch_market_clock_for

logger = logging.getLogger(__name__)


async def _fetch_market_clock(settings: Settings) -> MarketClock:
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        raise ValueError("Alpaca API credentials are required to check market hours")

    async with AlpacaClient(
        api_key=settings.alpaca_api_key,
        api_secret=settings.alpaca_api_secret,
        base_url=settings.alpaca_paper_base_url,
    ) as alpaca:
        return await fetch_market_clock_for(alpaca)


async def run_scheduled_cycles() -> None:
    """Run one scheduled tick, skipping when globally disabled or market-closed."""
    settings = get_settings()
    if settings.kill_switch_enabled:
        logger.warning("Scheduled agent tick skipped: kill switch is enabled")
        return

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentConfig.id).where(AgentConfig.enabled.is_(True))
            )
            agent_ids = result.scalars().all()
    except Exception:
        logger.exception("Scheduled agent tick failed while loading enabled agents")
        return

    if not agent_ids:
        return

    try:
        clock = await _fetch_market_clock(settings)
    except Exception:
        logger.exception("Scheduled agent tick skipped: market clock check failed")
        return

    market_hours = check_market_open(clock)
    if not market_hours.allowed:
        logger.info("Scheduled agent tick skipped: %s", market_hours.reason)
        return

    for agent_id in agent_ids:
        try:
            async with AsyncSessionLocal() as db:
                await run_agent_cycle(
                    agent_id,
                    db,
                    trigger=CycleTrigger.SCHEDULED,
                )
        except CycleOverlapError:
            logger.info(
                "Scheduled cycle skipped for agent %s: cycle already in progress",
                agent_id,
            )
        except Exception:
            logger.exception("Scheduled cycle failed for agent %s", agent_id)


def create_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    """Create an interval scheduler; application lifespan owns its start/stop."""
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scheduled_cycles,
        trigger="interval",
        minutes=settings.scheduler_interval_minutes,
        id="scheduled-agent-cycles",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
