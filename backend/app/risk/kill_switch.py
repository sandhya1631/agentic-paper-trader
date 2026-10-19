"""Database-backed global kill switch with an environment force-on override."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import SystemRuntimeControl

GLOBAL_CONTROL_KEY = "global"


async def get_kill_switch_control(
    db: AsyncSession,
) -> SystemRuntimeControl | None:
    result = await db.execute(
        select(SystemRuntimeControl).where(
            SystemRuntimeControl.key == GLOBAL_CONTROL_KEY
        )
    )
    return result.scalar_one_or_none()


async def is_kill_switch_enabled(db: AsyncSession) -> bool:
    if get_settings().kill_switch_enabled:
        return True
    control = await get_kill_switch_control(db)
    return bool(control and control.kill_switch_enabled)


class EnvironmentKillSwitchOverrideError(Exception):
    """Raised when an API request tries to disable an environment-forced switch."""


async def set_kill_switch(
    db: AsyncSession, *, enabled: bool, actor_id: UUID
) -> SystemRuntimeControl:
    if not enabled and get_settings().kill_switch_enabled:
        raise EnvironmentKillSwitchOverrideError(
            "KILL_SWITCH_ENABLED is true in the environment and cannot be disabled by API"
        )

    result = await db.execute(
        select(SystemRuntimeControl)
        .where(SystemRuntimeControl.key == GLOBAL_CONTROL_KEY)
        .with_for_update()
    )
    control = result.scalar_one_or_none()
    if control is None:
        control = SystemRuntimeControl(
            key=GLOBAL_CONTROL_KEY,
            kill_switch_enabled=enabled,
            updated_by=actor_id,
        )
        db.add(control)
    else:
        control.kill_switch_enabled = enabled
        control.updated_by = actor_id
    await db.commit()
    await db.refresh(control)
    return control
