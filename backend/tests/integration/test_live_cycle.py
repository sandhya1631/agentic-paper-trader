"""A full agent cycle against live services, with nothing submitted (#100).

run_agent_cycle on the test Postgres with real IEX bars, the real paper account (the
env ALPACA_* keys stored in the vault for the seeded user, as Settings -> Connect
would) and the configured LLM_PROVIDER. The agent is OBSERVE_ONLY, so a BUY/SELL ends
COMPLETED without evaluating policies or reaching the broker.

Needs ALPACA_INTEGRATION=1 plus the flag of the configured provider
(OLLAMA_INTEGRATION, OPENAI_INTEGRATION or ANTHROPIC_INTEGRATION). On a CPU-only
machine a local llama cycle can take minutes and may hit the client's 180 s timeout.
"""

import uuid

import pytest
from integration_gates import require_alpaca, require_llm
from sqlalchemy import func, select

from app.accounts.credential_vault import save_alpaca_credentials
from app.agent.orchestrator import run_agent_cycle
from app.core.config import get_settings
from app.db.enums import (
    ApprovalMode,
    CycleStatus,
    CycleTrigger,
    PolicyDecisionType,
    UserRole,
)
from app.db.models import AgentConfig, BrokerOrder, PolicySet, User

pytestmark = pytest.mark.integration

SYMBOL = "AAPL"


async def test_observe_only_cycle_runs_end_to_end_without_submitting(db_session):
    key, secret = require_alpaca()
    settings = get_settings()
    require_llm(settings.llm_provider.lower())

    user = User(email=f"it-cycle-{uuid.uuid4().hex[:8]}@example.com", password_hash="x",
                role=UserRole.OPERATOR)
    db_session.add(user)
    await db_session.commit()
    account = await save_alpaca_credentials(db_session, user.id, key, secret)
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()
    agent = AgentConfig(account_id=account.id, policy_set_id=policy_set.id,
                        name="Integration observe-only agent", watchlist=[SYMBOL],
                        strategy_version="S-001", approval_mode=ApprovalMode.OBSERVE_ONLY,
                        enabled=True, model_provider=settings.llm_provider,
                        model_name="configured")
    db_session.add(agent)
    await db_session.commit()

    cycle = await run_agent_cycle(agent.id, db_session, trigger=CycleTrigger.MANUAL)

    assert cycle.status in {CycleStatus.HOLD, CycleStatus.COMPLETED}, (
        cycle.status, cycle.failure_code, cycle.failure_message
    )
    assert cycle.completed_at is not None
    assert cycle.policy_decision == PolicyDecisionType.NOT_EVALUATED
    assert cycle.prompt_version == "S-001" and cycle.prompt_text
    assert cycle.llm_raw_response
    assert cycle.proposal_json["symbol"] == SYMBOL
    snapshot = cycle.snapshot_json
    assert snapshot["account"]["data_status"] == "LIVE"
    assert snapshot["market"][SYMBOL]["bar_count"] > 0
    assert snapshot["execution_status"] in {"NO_TRADE_PROPOSED", "OBSERVE_ONLY"}
    orders = await db_session.scalar(select(func.count()).select_from(BrokerOrder))
    assert orders == 0
