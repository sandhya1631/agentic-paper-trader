"""Tests for persisting a queryable policy decision on every agent cycle (#121)."""

import json
import uuid
from datetime import UTC, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import run_agent_cycle
from app.db.enums import (
    ApprovalMode,
    CycleStatus,
    CycleTrigger,
    PolicyDecisionType,
    PolicyOutcome,
    UserRole,
)
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.schemas import PolicyDecision, PolicyEvaluation


def _policy_decision(*, allowed: bool) -> PolicyDecision:
    """A realistic PolicyDecision (ALLOW with token, or DENY on a failed P-008)."""
    if allowed:
        evaluation = PolicyEvaluation(
            policy_id="P-001",
            policy_name="Paper-only broker",
            outcome=PolicyOutcome.PASS,
            reason="Paper endpoint",
            evidence={"is_paper": True},
        )
        return PolicyDecision(
            decision=PolicyDecisionType.ALLOW,
            policy_version="POLICY-1.0",
            evaluated_at=datetime.now(UTC),
            evaluations=[evaluation],
            final_quantity=1,
            policy_token="signed-policy-token",
        )
    evaluation = PolicyEvaluation(
        policy_id="P-008",
        policy_name="Available cash",
        outcome=PolicyOutcome.FAIL,
        reason="Insufficient cash",
        evidence={"cash": "0", "order_value": "1000"},
    )
    return PolicyDecision(
        decision=PolicyDecisionType.DENY,
        policy_version="POLICY-1.0",
        evaluated_at=datetime.now(UTC),
        evaluations=[evaluation],
        final_quantity=None,
        policy_token=None,
    )


def _mock_db_for_fresh_cycle() -> MagicMock:
    """MagicMock DB that satisfies the orchestrator's pre-flight queries (no Postgres)."""
    agent_result = MagicMock()
    no_active_cycle_result = MagicMock()
    no_active_cycle_result.scalars.return_value.first.return_value = None
    no_expired_approvals_result = MagicMock()
    no_expired_approvals_result.all.return_value = []

    def refresh(model):
        if isinstance(model, AgentCycle) and model.id is None:
            model.id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[agent_result, no_expired_approvals_result, no_active_cycle_result]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=refresh)
    return db, agent_result


def _llm(proposal: dict) -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=json.dumps(proposal))
    return llm


def _collector() -> MagicMock:
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    return collector


@pytest.mark.asyncio
async def test_non_auto_cycle_persists_policy_record():
    """A HOLD cycle (no order intent) records an explicit NOT_EVALUATED marker."""
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
    )
    db, agent_result = _mock_db_for_fresh_cycle()
    agent_result.scalar_one_or_none.return_value = agent

    cycle = await run_agent_cycle(
        agent.id,
        db,
        llm_client=_llm({"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "flat"}),
        collector=_collector(),
    )

    assert cycle.status == CycleStatus.HOLD
    assert cycle.policy_decision == PolicyDecisionType.NOT_EVALUATED
    assert cycle.policy_result_json is None


@pytest.mark.asyncio
async def test_auto_cycle_still_evaluates_and_submits(monkeypatch):
    """AUTO path is unchanged (evaluate + submit) AND the decision is persisted."""
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.AUTO,
    )
    db, agent_result = _mock_db_for_fresh_cycle()
    agent_result.scalar_one_or_none.return_value = agent

    broker_order = SimpleNamespace(status=SimpleNamespace(value="SUBMITTED"))
    execute_trade = AsyncMock(return_value=(_policy_decision(allowed=True), broker_order))
    monkeypatch.setattr(
        "app.agent.orchestrator.evaluate_and_submit_cycle_trade", execute_trade
    )

    cycle = await run_agent_cycle(
        agent.id,
        db,
        trigger=CycleTrigger.SCHEDULED,
        llm_client=_llm({"symbol": "AAPL", "action": "BUY", "quantity": 1, "reasoning": "buy"}),
        collector=_collector(),
    )

    execute_trade.assert_awaited_once_with(db, agent, cycle)
    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.policy_decision == PolicyDecisionType.ALLOW
    assert cycle.policy_result_json is not None
    # The signed token is never persisted in the audit record.
    assert "policy_token" not in cycle.policy_result_json
    assert cycle.policy_result_json["evaluations"][0]["policy_id"] == "P-001"


@pytest.mark.asyncio
async def test_audit_record_does_not_bypass_execution_gate(monkeypatch):
    """Persisting the policy record reuses the single gate evaluation; it adds no
    second evaluation and never flips a denied cycle into an executed one."""
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.AUTO,
    )
    db, agent_result = _mock_db_for_fresh_cycle()
    agent_result.scalar_one_or_none.return_value = agent

    execute_trade = AsyncMock(return_value=(_policy_decision(allowed=False), None))
    monkeypatch.setattr(
        "app.agent.orchestrator.evaluate_and_submit_cycle_trade", execute_trade
    )

    cycle = await run_agent_cycle(
        agent.id,
        db,
        trigger=CycleTrigger.SCHEDULED,
        llm_client=_llm({"symbol": "AAPL", "action": "BUY", "quantity": 1, "reasoning": "buy"}),
        collector=_collector(),
    )

    # Exactly one evaluation (the gate); the audit record is derived from it, not a re-run.
    execute_trade.assert_awaited_once()
    assert cycle.status == CycleStatus.REJECTED
    assert cycle.failure_code == "POLICY_DENIED"
    assert cycle.policy_decision == PolicyDecisionType.DENY


@pytest.mark.asyncio
async def test_persisted_policy_queryable_by_outcome(db_session: AsyncSession):
    """Cycles are selectable by their persisted policy decision, and the failing
    policy is recoverable from policy_result_json (supports #82 filter-by-policy)."""
    user = User(email="policy-query@example.com", password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()
    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db_session.add(account)
    await db_session.flush()
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()
    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Query Agent",
        watchlist=["AAPL"],
        strategy_version="S-001",
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    allowed = _policy_decision(allowed=True)
    denied = _policy_decision(allowed=False)
    db_session.add_all(
        [
            AgentCycle(
                agent_id=agent.id,
                trigger=CycleTrigger.SCHEDULED,
                status=CycleStatus.COMPLETED,
                started_at=datetime.now(timezone.utc),
                policy_decision=PolicyDecisionType.ALLOW,
                policy_result_json=allowed.model_dump(mode="json", exclude={"policy_token"}),
            ),
            AgentCycle(
                agent_id=agent.id,
                trigger=CycleTrigger.SCHEDULED,
                status=CycleStatus.REJECTED,
                started_at=datetime.now(timezone.utc),
                policy_decision=PolicyDecisionType.DENY,
                policy_result_json=denied.model_dump(mode="json", exclude={"policy_token"}),
            ),
        ]
    )
    await db_session.commit()

    result = await db_session.execute(
        select(AgentCycle).where(AgentCycle.policy_decision == PolicyDecisionType.DENY.value)
    )
    denied_cycles = result.scalars().all()
    assert len(denied_cycles) == 1
    failed = [
        e
        for e in denied_cycles[0].policy_result_json["evaluations"]
        if e["outcome"] == PolicyOutcome.FAIL.value
    ]
    assert [e["policy_id"] for e in failed] == ["P-008"]
