import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ApprovalStatus, CycleStatus, CycleTrigger, UserRole
from app.db.models import (
    AgentConfig,
    AgentCycle,
    CycleApproval,
    PolicySet,
    TradingAccount,
    User,
)
from app.db.schema import (
    ensure_agent_cycle_prompt_columns,
    ensure_policy_token_scrubbed,
    ensure_user_scheduler_column,
)


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


async def test_existing_users_table_gets_scheduler_column(db_session: AsyncSession):
    await db_session.execute(text("ALTER TABLE users DROP COLUMN scheduler_enabled"))

    connection = await db_session.connection()
    await ensure_user_scheduler_column(connection)
    await ensure_user_scheduler_column(connection)

    result = await db_session.execute(
        text(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'users' "
            "AND column_name = 'scheduler_enabled'"
        )
    )
    column = result.one()

    assert column.column_name == "scheduler_enabled"
    assert column.is_nullable == "NO"
    assert "true" in column.column_default


async def _seed_agent(db: AsyncSession) -> AgentConfig:
    user = User(email="scrub@example.com", password_hash="hash", role=UserRole.OPERATOR)
    db.add(user)
    await db.flush()
    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db.add(account)
    await db.flush()
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db.add(policy_set)
    await db.flush()
    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Scrub Agent",
        watchlist=["AAPL"],
        strategy_version="S-001",
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db.add(agent)
    await db.flush()
    return agent


async def test_existing_policy_tokens_are_scrubbed(db_session: AsyncSession):
    """Rows written before #175 lose their stored policy_token; nothing else changes."""
    agent = await _seed_agent(db_session)
    evaluations = [{"policy_id": "P-001", "outcome": "PASS"}]
    allowed = {"decision": "ALLOW", "evaluations": evaluations, "final_quantity": 1}
    denied = {"decision": "DENY", "evaluations": evaluations, "final_quantity": None}

    def cycle(snapshot, policy_result) -> AgentCycle:
        return AgentCycle(
            agent_id=agent.id,
            trigger=CycleTrigger.SCHEDULED,
            status=CycleStatus.COMPLETED,
            started_at=datetime.now(UTC),
            snapshot_json=snapshot,
            policy_result_json=policy_result,
        )

    allowed_cycle = cycle(
        {
            "execution_status": "ORDER_SUBMITTED",
            "policy_evaluation": {**allowed, "policy_token": "signed-token"},
        },
        {**allowed, "policy_token": "signed-token"},
    )
    denied_cycle = cycle(
        {
            "execution_status": "POLICY_DENIED",
            "policy_evaluation": {**denied, "policy_token": None},
        },
        denied,
    )
    clean_cycle = cycle({"execution_status": "NO_TRADE_PROPOSED"}, None)
    db_session.add_all([allowed_cycle, denied_cycle, clean_cycle])
    await db_session.flush()
    db_session.add_all(
        [
            CycleApproval(
                cycle_id=allowed_cycle.id,
                status=ApprovalStatus.APPROVED,
                proposal_hash="0" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                policy_result_json={**allowed, "policy_token": "signed-token"},
            ),
            CycleApproval(
                cycle_id=denied_cycle.id,
                status=ApprovalStatus.REJECTED,
                proposal_hash="1" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                policy_result_json=None,
            ),
        ]
    )
    await db_session.commit()

    connection = await db_session.connection()
    await ensure_policy_token_scrubbed(connection)
    await ensure_policy_token_scrubbed(connection)  # idempotent

    cycles = {
        row.id: row
        for row in await db_session.execute(
            select(AgentCycle.id, AgentCycle.snapshot_json, AgentCycle.policy_result_json)
        )
    }
    assert cycles[allowed_cycle.id].snapshot_json == {
        "execution_status": "ORDER_SUBMITTED",
        "policy_evaluation": allowed,
    }
    assert cycles[allowed_cycle.id].policy_result_json == allowed
    assert cycles[denied_cycle.id].snapshot_json == {
        "execution_status": "POLICY_DENIED",
        "policy_evaluation": denied,
    }
    assert cycles[denied_cycle.id].policy_result_json == denied
    assert cycles[clean_cycle.id].snapshot_json == {"execution_status": "NO_TRADE_PROPOSED"}
    assert cycles[clean_cycle.id].policy_result_json is None

    approvals = {
        row.cycle_id: row.policy_result_json
        for row in await db_session.execute(
            select(CycleApproval.cycle_id, CycleApproval.policy_result_json)
        )
    }
    assert approvals == {allowed_cycle.id: allowed, denied_cycle.id: None}

    raw = await db_session.execute(
        text(
            "SELECT snapshot_json::text, policy_result_json::text FROM agent_cycles "
            "UNION ALL SELECT policy_result_json::text, NULL FROM cycle_approvals"
        )
    )
    assert all("policy_token" not in (value or "") for row in raw for value in row)


# Raw JSON text, stored as-is in the JSON columns. A jsonb round trip would reorder
# keys and normalize spacing, so comparing the text proves a row was never rewritten.
# "\\u0000" (Python source) is the 6-character JSON escape \u0000: valid json, but
# jsonb rejects it.
UNTOUCHED_SNAPSHOTS = {
    "ordinary": '{"execution_status": "HOLD",   "b": 1, "a": [2, 3]}',
    "nul_escape": '{"note": "nul \\u0000 here",  "execution_status": "HOLD"}',
    # Can't be parsed as jsonb, so it is skipped (left as is) rather than abort startup.
    "nul_escape_and_token": '{"note": "\\u0000",  "policy_evaluation": {"policy_token": "t"}}',
    "array": '["policy_token",  1]',
    "scalar": '"policy_token"',
    "json_null": "null",
    "sql_null": None,
}


async def test_policy_token_scrub_leaves_other_rows_untouched(db_session: AsyncSession):
    """Rows without the key (or that jsonb can't parse) are never cast or rewritten, so
    the startup patch stays cheap and can't fail; a token row in the same run is still
    scrubbed, and a second run changes nothing."""
    agent = await _seed_agent(db_session)
    names = [*UNTOUCHED_SNAPSHOTS, "token"]
    cycles = {
        name: AgentCycle(
            agent_id=agent.id,
            trigger=CycleTrigger.SCHEDULED,
            status=CycleStatus.COMPLETED,
            started_at=datetime.now(UTC),
        )
        for name in names
    }
    db_session.add_all(cycles.values())
    await db_session.flush()
    approval = CycleApproval(
        cycle_id=cycles["ordinary"].id,
        status=ApprovalStatus.APPROVED,
        proposal_hash="0" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(approval)
    await db_session.flush()

    set_cycle = text(
        "UPDATE agent_cycles SET snapshot_json = CAST(:snapshot AS json), "
        "policy_result_json = CAST(:policy AS json) WHERE id = :id"
    )
    for name, raw in UNTOUCHED_SNAPSHOTS.items():
        # The same raw text in both columns, so both cycle statements are exercised.
        await db_session.execute(set_cycle, {"snapshot": raw, "policy": raw, "id": cycles[name].id})
    token_snapshot = '{"z": 1,  "policy_evaluation": {"policy_token": "t", "decision": "ALLOW"}}'
    await db_session.execute(
        set_cycle,
        {
            "snapshot": token_snapshot,
            "policy": '{"policy_token": "t",  "decision": "ALLOW"}',
            "id": cycles["token"].id,
        },
    )
    await db_session.execute(
        text(
            "UPDATE cycle_approvals SET policy_result_json = CAST(:policy AS json) "
            "WHERE id = :id"
        ),
        {"policy": '{"note": "\\u0000",  "policy_token": "t"}', "id": approval.id},
    )
    await db_session.commit()

    read_cycles = text(
        "SELECT id, snapshot_json::text AS snapshot, policy_result_json::text AS policy "
        "FROM agent_cycles"
    )
    by_id = {cycle.id: name for name, cycle in cycles.items()}
    before = {by_id[row.id]: row for row in await db_session.execute(read_cycles)}
    assert before["nul_escape"].snapshot == UNTOUCHED_SNAPSHOTS["nul_escape"]  # stored as-is

    connection = await db_session.connection()
    await ensure_policy_token_scrubbed(connection)
    await ensure_policy_token_scrubbed(connection)  # idempotent

    after = {by_id[row.id]: row for row in await db_session.execute(read_cycles)}
    for name in UNTOUCHED_SNAPSHOTS:
        assert after[name].snapshot == before[name].snapshot, name
        assert after[name].policy == before[name].policy, name
    token_row = (
        await db_session.execute(
            select(AgentCycle.snapshot_json, AgentCycle.policy_result_json).where(
                AgentCycle.id == cycles["token"].id
            )
        )
    ).one()
    assert token_row.snapshot_json == {"z": 1, "policy_evaluation": {"decision": "ALLOW"}}
    assert token_row.policy_result_json == {"decision": "ALLOW"}
    approval_text = await db_session.scalar(
        text("SELECT policy_result_json::text FROM cycle_approvals WHERE id = :id"),
        {"id": approval.id},
    )
    assert approval_text == '{"note": "\\u0000",  "policy_token": "t"}'  # skipped, not fatal
