import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agent.router import approve_cycle_trade, reject_cycle_trade
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


# --- Issue #77 Definition of Done: actor+time, expiry, proposal immutability, reject ---


@pytest.mark.asyncio
async def test_approve_records_actor_and_time(monkeypatch):
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
    actor_id = uuid4()
    before = datetime.now(UTC)

    await approve_cycle_trade(
        approval.id,
        ApprovalAction(comment="looks good"),
        db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
        current_user=SimpleNamespace(id=actor_id),
    )

    assert approval.actor_id == actor_id
    assert approval.acted_at is not None
    assert before <= approval.acted_at <= datetime.now(UTC)
    assert approval.comment == "looks good"


@pytest.mark.asyncio
async def test_expired_approval_cannot_execute(monkeypatch):
    approval, cycle, agent = _approval_fixture()
    approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.agent.router.evaluate_and_submit_cycle_trade", execute)

    with pytest.raises(HTTPException) as exc_info:
        await approve_cycle_trade(
            approval.id,
            ApprovalAction(),
            db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert approval.status == ApprovalStatus.EXPIRED
    assert cycle.status == CycleStatus.REJECTED
    assert cycle.failure_code == "APPROVAL_EXPIRED"
    execute.assert_not_awaited()  # an expired approval must never reach the broker


@pytest.mark.asyncio
async def test_proposal_cannot_be_edited_via_approval_endpoint(monkeypatch):
    """The approval only carries proposal_hash; if cycle.proposal_json has since
    drifted from what was hashed at parking time, approval must refuse to execute
    the now-different proposal rather than silently approving whatever is current."""
    approval, cycle, agent = _approval_fixture()
    cycle.proposal_json = {**cycle.proposal_json, "quantity": 999}  # tampered post-parking
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.agent.router.evaluate_and_submit_cycle_trade", execute)

    with pytest.raises(HTTPException) as exc_info:
        await approve_cycle_trade(
            approval.id,
            ApprovalAction(),
            db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert approval.status == ApprovalStatus.PENDING  # never silently approved
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_sets_rejected_and_no_order(monkeypatch):
    approval, cycle, agent = _approval_fixture()
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.agent.router.evaluate_and_submit_cycle_trade", execute)
    actor_id = uuid4()

    result = await reject_cycle_trade(
        approval.id,
        ApprovalAction(comment="not confident in this signal"),
        db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
        current_user=SimpleNamespace(id=actor_id),
    )

    assert result.status == CycleStatus.REJECTED
    assert approval.status == ApprovalStatus.REJECTED
    assert approval.actor_id == actor_id
    assert approval.acted_at is not None
    assert cycle.snapshot_json["execution_status"] == "HUMAN_REJECTED"
    execute.assert_not_awaited()  # rejecting must never touch the broker


@pytest.mark.asyncio
async def test_approve_on_an_already_decided_approval_is_a_conflict(monkeypatch):
    """Models the outcome the FOR UPDATE row lock guarantees: once one decision has
    landed, a second approve/reject attempt on the same approval is refused rather
    than silently re-executing. (The lock itself needs a real-DB integration test;
    this proves the status guard that makes its outcome correct.)"""
    approval, cycle, agent = _approval_fixture()
    approval.status = ApprovalStatus.APPROVED
    monkeypatch.setattr(
        "app.agent.router._load_owned_approval",
        AsyncMock(return_value=(approval, cycle, agent)),
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.agent.router.evaluate_and_submit_cycle_trade", execute)

    with pytest.raises(HTTPException) as exc_info:
        await approve_cycle_trade(
            approval.id,
            ApprovalAction(),
            db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 409
    execute.assert_not_awaited()
