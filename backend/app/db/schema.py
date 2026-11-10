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


async def ensure_policy_token_scrubbed(conn: AsyncConnection) -> None:
    """Remove signed policy tokens that older code stored with policy decisions (#175).

    The token must exist only in memory between the policy engine and the broker
    gateway. Rows written before the fix kept it in a cycle's
    ``snapshot_json -> 'policy_evaluation'`` and an approval's ``policy_result_json``;
    a cycle's own ``policy_result_json`` is scrubbed too, for completeness. The key is
    removed whatever its value (a DENY stored ``null``). Idempotent: only rows that
    still carry the key are updated. The columns are ``JSON``, so each statement goes
    through ``jsonb`` and back. Runs at startup, in ``main.py``'s lifespan.
    """
    await conn.execute(
        text(
            "UPDATE agent_cycles "
            "SET snapshot_json = "
            "(snapshot_json::jsonb #- '{policy_evaluation,policy_token}')::json "
            "WHERE snapshot_json::jsonb #> '{policy_evaluation,policy_token}' IS NOT NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE agent_cycles "
            "SET policy_result_json = (policy_result_json::jsonb - 'policy_token')::json "
            "WHERE policy_result_json::jsonb -> 'policy_token' IS NOT NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE cycle_approvals "
            "SET policy_result_json = (policy_result_json::jsonb - 'policy_token')::json "
            "WHERE policy_result_json::jsonb -> 'policy_token' IS NOT NULL"
        )
    )
