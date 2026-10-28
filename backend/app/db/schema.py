from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import DEFAULT_PROMPT_REVISION, DEFAULT_STRATEGY_VERSION


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
            f"NOT NULL DEFAULT '{DEFAULT_PROMPT_REVISION}'"
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
