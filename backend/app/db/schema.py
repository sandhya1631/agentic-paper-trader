from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_agent_cycle_prompt_columns(conn: AsyncConnection) -> None:
    """Add prompt metadata columns to databases created before they were introduced."""
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(30) "
            "NOT NULL DEFAULT 'S-001'"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE agent_cycles "
            "ADD COLUMN IF NOT EXISTS prompt_revision VARCHAR(30) "
            "NOT NULL DEFAULT '1.0.0'"
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
