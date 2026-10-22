import asyncio

from app.celery_app import celery_app
from app.db.session import engine


def _run_async(coroutine):
    async def run_and_dispose():
        try:
            return await coroutine
        finally:
            await engine.dispose()

    return asyncio.run(run_and_dispose())


@celery_app.task(name="app.agent.run_scheduled_cycles")
def run_scheduled_cycles_task() -> dict:
    from app.agent.scheduler import run_scheduled_cycles

    result = _run_async(run_scheduled_cycles())
    return result.model_dump(mode="json")


@celery_app.task(name="app.agent.run_agent_schedule")
def run_agent_schedule_task(schedule_id: str) -> dict | None:
    from uuid import UUID

    from app.agent.scheduler import run_agent_schedule

    result = _run_async(run_agent_schedule(UUID(schedule_id)))
    return result.model_dump(mode="json") if result is not None else None
