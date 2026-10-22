import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.router import approve_cycle_trade
from app.db.enums import ApprovalStatus, BrokerOrderStatus, CycleStatus, PolicyOutcome
from app.schemas import ApprovalAction, PolicyDecision, PolicyEvaluation


def _approval_fixture():
    proposal = {
        "symbol": "AAPL",
        "action": "BUY",
        "quantity": 1,
        "reasoning": "Test",
    }
    cycle_id = uuid4()
    approval = SimpleNamespace(
        id=uuid4(),
        cycle_id=cycle_id,
        status=ApprovalStatus.PENDING,
        proposal_hash=hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        acted_at=None,
        actor_id=None,
        comment=None,
        policy_version=None,
        policy_result_json=None,
    )
    cycle = SimpleNamespace(
        id=cycle_id,
        agent_id=uuid4(),
        status=CycleStatus.PENDING_APPROVAL,
        proposal_json=proposal,
        snapshot_json={"execution_status": "PENDING_APPROVAL"},
        completed_at=None,
        failure_code=None,
        failure_message=None,
    )
    agent = SimpleNamespace(id=uuid4())
    return approval, cycle, agent


def _policy_decision(*, allowed: bool) -> PolicyDecision:
    outcome = PolicyOutcome.PASS if allowed else PolicyOutcome.FAIL
    evaluation = PolicyEvaluation(
        policy_id="P-001",
        policy_name="Paper-only broker",
        outcome=outcome,
        reason="OK" if allowed else "Not paper",
        evidence={},
    )
    return PolicyDecision(
        decision="ALLOW" if allowed else "DENY",
        policy_version="POLICY-1.0",
        evaluated_at=datetime.now(UTC),
        evaluations=[evaluation],
        final_quantity=1 if allowed else None,
        policy_token="signed-policy-token" if allowed else None,
    )


@pytest.mark.asyncio
async def test_approving_pending_cycle_submits_after_policy_allow(monkeypatch):
    approval, cycle, agent = _approval_fixture()
    order = SimpleNamespace(status=BrokerOrderStatus.SUBMITTED)
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    monkeypatch.setattr(
        "app.agent.router.evaluate_and_submit_cycle_trade",
        AsyncMock(return_value=(_policy_decision(allowed=True), order)),
    )
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    result = await approve_cycle_trade(
        approval.id,
        ApprovalAction(comment="approved"),
        db=db,
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert result.status == CycleStatus.COMPLETED
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.policy_version == "POLICY-1.0"
    assert cycle.snapshot_json["execution_status"] == "ORDER_SUBMITTED"


@pytest.mark.asyncio
async def test_policy_denial_after_approval_never_marks_trade_submitted(monkeypatch):
    approval, cycle, agent = _approval_fixture()
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    monkeypatch.setattr(
        "app.agent.router.evaluate_and_submit_cycle_trade",
        AsyncMock(return_value=(_policy_decision(allowed=False), None)),
    )

    result = await approve_cycle_trade(
        approval.id,
        ApprovalAction(),
        db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert result.status == CycleStatus.REJECTED
    assert approval.status == ApprovalStatus.REJECTED
    assert cycle.snapshot_json["execution_status"] == "POLICY_DENIED"
