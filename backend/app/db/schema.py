from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import DEFAULT_STRATEGY_VERSION

# Revision backfilled onto cycles created before prompt-revision tracking existed —
# they ran the original S-001 prompt, so they must not take the current revision.
LEGACY_PROMPT_REVISION = "1.0.0"


async def ensure_agent_cycle_prompt_columns(conn: AsyncConnection) -> None:
    """Add prompt metadata columns to databases created before they were introduced."""
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(30) "
            f"NOT NULL DEFAULT '{DEFAULT_STRATEGY_VERSION}'"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS prompt_revision VARCHAR(30) "
            f"NOT NULL DEFAULT '{LEGACY_PROMPT_REVISION}'"
        )
    )


async def ensure_agent_cycle_policy_columns(conn: AsyncConnection) -> None:
    """Add the per-cycle policy audit columns (#121) to pre-existing databases."""
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS policy_decision VARCHAR(20)"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS policy_result_json JSON"
        )
    )


async def ensure_user_scheduler_column(conn: AsyncConnection) -> None:
    """Add the per-user scheduler control to databases created before it existed."""
    await conn.execute(
        text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS scheduler_enabled BOOLEAN "
            "NOT NULL DEFAULT TRUE"
        )
    )


async def ensure_kill_switch_event_scope_columns(conn: AsyncConnection) -> None:
    """Add the user-level kill-switch scope columns to pre-existing databases."""
    await conn.execute(
        text(
            "ALTER TABLE kill_switch_events "
            "ADD COLUMN IF NOT EXISTS scope VARCHAR(10) NOT NULL DEFAULT 'GLOBAL'"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE kill_switch_events "
            "ADD COLUMN IF NOT EXISTS target_user_id UUID "
            "REFERENCES users(id) ON DELETE SET NULL"
        )
    )
