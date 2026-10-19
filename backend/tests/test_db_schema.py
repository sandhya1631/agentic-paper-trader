from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import ensure_agent_cycle_prompt_columns


async def test_existing_agent_cycles_table_gets_prompt_columns(db_session: AsyncSession):
    await db_session.execute(
        text(
            "ALTER TABLE agent_cycles "
            "DROP COLUMN prompt_version, DROP COLUMN prompt_revision"
        )
    )

    connection = await db_session.connection()
    await ensure_agent_cycle_prompt_columns(connection)
    await ensure_agent_cycle_prompt_columns(connection)

    result = await db_session.execute(
        text(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'agent_cycles' "
            "AND column_name IN ('prompt_version', 'prompt_revision')"
        )
    )
    columns = {row.column_name: row for row in result}

    assert set(columns) == {"prompt_version", "prompt_revision"}
    assert columns["prompt_version"].is_nullable == "NO"
    assert "S-001" in columns["prompt_version"].column_default
    assert columns["prompt_revision"].is_nullable == "NO"
    assert "1.0.0" in columns["prompt_revision"].column_default
