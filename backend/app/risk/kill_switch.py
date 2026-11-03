"""Database-backed global kill switch with an environment force-on override."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import KillSwitchEvent, SystemRuntimeControl

GLOBAL_CONTROL_KEY = "global"

OUTCOME_APPLIED = "APPLIED"
OUTCOME_REJECTED_ENV_OVERRIDE = "REJECTED_ENV_OVERRIDE"


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


async def get_latest_activation_reason(db: AsyncSession) -> str | None:
    """The reason given the last time the switch was turned on (None if never)."""
    result = await db.execute(
        select(KillSwitchEvent.reason)
        .where(
            KillSwitchEvent.outcome == OUTCOME_APPLIED,
            KillSwitchEvent.new_enabled.is_(True),
        )
        .order_by(KillSwitchEvent.occurred_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_kill_switch_events(db: AsyncSession, limit: int = 50) -> list[KillSwitchEvent]:
    result = await db.execute(
        select(KillSwitchEvent).order_by(KillSwitchEvent.occurred_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def set_kill_switch(
    db: AsyncSession,
    *,
    enabled: bool,
    actor_id: UUID,
    reason: str | None = None,
) -> SystemRuntimeControl:
    """Change the switch and write the audit row in the SAME transaction."""
    if not enabled and get_settings().kill_switch_enabled:
        # Refused, but still audited: who tried to switch trading back on, and when.
        db.add(
            KillSwitchEvent(
                actor_id=actor_id,
                previous_enabled=True,
                new_enabled=False,
                outcome=OUTCOME_REJECTED_ENV_OVERRIDE,
                reason=reason,
            )
        )
        await db.commit()
        raise EnvironmentKillSwitchOverrideError(
            "KILL_SWITCH_ENABLED is true in the environment and cannot be disabled by API"
        )

    result = await db.execute(
        select(SystemRuntimeControl)
        .where(SystemRuntimeControl.key == GLOBAL_CONTROL_KEY)
        .with_for_update()
    )
    control = result.scalar_one_or_none()
    previous_enabled = bool(control and control.kill_switch_enabled)
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
    db.add(
        KillSwitchEvent(
            actor_id=actor_id,
            previous_enabled=previous_enabled,
            new_enabled=enabled,
            outcome=OUTCOME_APPLIED,
            reason=reason,
        )
    )
    await db.commit()
    await db.refresh(control)
    return control