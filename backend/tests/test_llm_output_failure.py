"""A second invalid LLM answer ends the cycle; nothing is guessed (Story 2.2.2 / Issue #27)."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import orchestrator
from app.agent.orchestrator import run_agent_cycle
from app.db.enums import CycleStatus
from app.db.models import AgentCycle

INVALID_1 = "I think we should buy 5 shares of SPY because RSI is low."
INVALID_2 = '{"symbol": "SPY", "action": "BUY"}'  # valid JSON, but missing required fields
VALID_HOLD = json.dumps(
    {"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "No clear signal."}
)


def _run_inputs(*llm_responses: str):
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=None,
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
    llm.generate = AsyncMock(side_effect=list(llm_responses))
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    return agent, db, llm, collector


async def _run(*llm_responses: str):
    agent, db, llm, collector = _run_inputs(*llm_responses)
    cycle = await run_agent_cycle(agent.id, db, llm_client=llm, collector=collector)
    return cycle, db, llm


@pytest.mark.asyncio
async def test_cycle_ends_when_the_repair_also_fails():
    cycle, _, llm = await _run(INVALID_1, INVALID_2)

    assert llm.generate.await_count == 2
    assert cycle.status == CycleStatus.FAILED
    assert cycle.failure_code == "LLM_OUTPUT_INVALID"
    assert cycle.failure_message
    assert cycle.completed_at is not None


@pytest.mark.asyncio
async def test_no_proposal_symbol_or_quantity_is_invented():
    cycle, _, _ = await _run(INVALID_1, INVALID_2)

    assert cycle.proposal_json is None
    assert cycle.approval is None


@pytest.mark.asyncio
async def test_both_raw_responses_are_kept_for_the_audit_trail():
    cycle, _, _ = await _run(INVALID_1, INVALID_2)

    assert INVALID_1 in cycle.llm_raw_response
    assert INVALID_2 in cycle.llm_raw_response


@pytest.mark.asyncio
async def test_only_one_repair_attempt_is_ever_made():
    _, _, llm = await _run(INVALID_1, INVALID_2, INVALID_1)

    assert llm.generate.await_count == 2  # the third answer is never requested


@pytest.mark.asyncio
async def test_invalid_output_never_reaches_approval_or_the_broker(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", execute)
    agent, db, llm, collector = _run_inputs(INVALID_1, INVALID_2)

    await run_agent_cycle(agent.id, db, llm_client=llm, collector=collector)

    execute.assert_not_awaited()
    added = [type(call.args[0]) for call in db.add.call_args_list]
    assert added == [AgentCycle]  # no approval row, nothing else


@pytest.mark.asyncio
async def test_a_successful_repair_still_works():
    cycle, _, llm = await _run(INVALID_1, VALID_HOLD)

    assert llm.generate.await_count == 2
    assert cycle.status == CycleStatus.HOLD
    assert cycle.proposal_json["action"] == "HOLD"
    assert cycle.failure_code is None


@pytest.mark.asyncio
async def test_a_real_hold_from_the_llm_is_not_treated_as_a_failure():
    cycle, _, llm = await _run(VALID_HOLD)

    assert llm.generate.await_count == 1
    assert cycle.status == CycleStatus.HOLD
    assert cycle.failure_code is None