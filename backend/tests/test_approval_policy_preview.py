"""A parked proposal shows its policy results and expiry (Story 3.3.1 / Issue #76)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_cycle_approval import _trade_fixtures
from test_cycle_approval_routes import _approval_fixture, _policy_decision

from app.agent import approval, orchestrator
from app.agent.orchestrator import run_agent_cycle
from app.agent.router import list_pending_approvals
from app.core.config import Settings
from app.db.enums import ApprovalMode, ApprovalStatus, CycleStatus, PolicyDecisionType
from app.db.models import CycleApproval

VALID_BUY = json.dumps(
    {"symbol": "AAPL", "action": "BUY", "quantity": 3, "reasoning": "RSI is 35, above SMA-20."}
)


def _required_mode_inputs():
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
    )
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent
    no_active_cycle_result = MagicMock()
    no_active_cycle_result.scalars.return_value.first.return_value = None
    no_expired_approvals_result = MagicMock()
    no_expired_approvals_result.all.return_value = []

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[agent_result, no_expired_approvals_result, no_active_cycle_result]
    )
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    llm = MagicMock()
    llm.generate = AsyncMock(return_value=VALID_BUY)
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    return agent, db, llm, collector


async def _park_a_proposal():
    agent, db, llm, collector = _required_mode_inputs()
    cycle = await run_agent_cycle(agent.id, db, llm_client=llm, collector=collector)
    parked = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], CycleApproval)]
    assert len(parked) == 1
    return cycle, parked[0]


# --- the pending approval carries the policy evidence and an expiry ---


@pytest.mark.asyncio
async def test_parked_proposal_stores_the_policy_results(monkeypatch):
    decision = _policy_decision(allowed=True)
    dry_run = AsyncMock(return_value=(decision, None))
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", dry_run)

    cycle, parked = await _park_a_proposal()

    assert cycle.status == CycleStatus.PENDING_APPROVAL
    assert parked.status == ApprovalStatus.PENDING
    assert parked.policy_version == decision.policy_version
    assert parked.policy_result_json["decision"] == "ALLOW"
    assert parked.policy_result_json["evaluations"][0]["policy_id"] == "P-001"
    assert dry_run.await_args.kwargs["submit"] is False  # nothing is sent to the broker


@pytest.mark.asyncio
async def test_the_signed_policy_token_is_not_stored_or_shown(monkeypatch):
    dry_run = AsyncMock(return_value=(_policy_decision(allowed=True), None))
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", dry_run)

    _, parked = await _park_a_proposal()

    assert "policy_token" not in parked.policy_result_json


@pytest.mark.asyncio
async def test_a_failing_policy_is_still_shown_to_the_operator(monkeypatch):
    dry_run = AsyncMock(return_value=(_policy_decision(allowed=False), None))
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", dry_run)

    _, parked = await _park_a_proposal()

    assert parked.policy_result_json["decision"] == PolicyDecisionType.DENY.value
    assert parked.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_proposal_is_still_parked_when_the_preview_is_unavailable(monkeypatch):
    dry_run = AsyncMock(side_effect=RuntimeError("alpaca is down"))
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", dry_run)

    cycle, parked = await _park_a_proposal()

    assert cycle.status == CycleStatus.PENDING_APPROVAL
    assert parked.policy_result_json is None
    assert parked.policy_version is None


# --- expiry is configurable ---


@pytest.mark.asyncio
async def test_expiry_comes_from_the_approval_expiry_setting(monkeypatch):
    dry_run = AsyncMock(return_value=(_policy_decision(allowed=True), None))
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", dry_run)
    monkeypatch.setattr(
        orchestrator,
        "get_settings",
        lambda: Settings(_env_file=None, approval_expiry_minutes=30),
    )

    _, parked = await _park_a_proposal()

    remaining = parked.expires_at - datetime.now(UTC)
    assert timedelta(minutes=29) < remaining <= timedelta(minutes=30)


def test_default_expiry_is_24_hours_and_must_be_positive():
    assert Settings(_env_file=None).approval_expiry_minutes == 1440
    with pytest.raises(ValueError):
        Settings(_env_file=None, approval_expiry_minutes=0)


# --- the pending list returns the policy evidence ---


@pytest.mark.asyncio
async def test_pending_list_shows_policies_and_expiry():
    pending, cycle, _ = _approval_fixture()
    pending.requested_at = datetime.now(UTC)
    pending.policy_version = "POLICY-1.0"
    pending.policy_result_json = {"decision": "ALLOW", "evaluations": [{"policy_id": "P-001"}]}
    rows = SimpleNamespace(all=lambda: [(pending, cycle.proposal_json)])
    db = SimpleNamespace(execute=AsyncMock(return_value=rows))

    result = await list_pending_approvals(db=db, current_user=SimpleNamespace(id=uuid.uuid4()))

    shown = result[0].approval
    assert shown.policy_version == "POLICY-1.0"
    assert shown.policy_result_json["decision"] == "ALLOW"
    assert shown.expires_at == pending.expires_at


# --- the dry run never reaches the broker ---


@pytest.mark.asyncio
async def test_dry_run_evaluates_policies_without_submitting(monkeypatch):
    db, agent, cycle, _ = _trade_fixtures(monkeypatch, market_open=True)
    submit_order = AsyncMock()
    monkeypatch.setattr(approval, "submit_approved_order", submit_order)

    decision, order = await approval.evaluate_and_submit_cycle_trade(
        db, agent, cycle, submit=False
    )

    assert decision.decision == PolicyDecisionType.ALLOW
    assert order is None
    submit_order.assert_not_awaited()