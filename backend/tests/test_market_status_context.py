"""Market status in the agent context and the S-001 account-data rules (#172).

Builds on the live portfolio context (statuses LIVE / UNAVAILABLE_*): the model now
also learns whether the market is open, and the default S-001 prompt tells it to HOLD
unless the account data is LIVE and to SELL only what it owns.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import orchestrator
from app.agent.context import build_agent_context
from app.agent.orchestrator import _load_portfolio_context, run_agent_cycle
from app.agent.prompt import get_prompt_definition, get_system_prompt

LIVE_ACCOUNT = {
    "cash": "52000.50",
    "equity": "98000.25",
    "buying_power": "104000",
    "daily_pnl": "-1",
}


# --- context builder ---


def test_market_status_is_open_closed_or_unknown():
    open_ctx = build_agent_context(["AAPL"], {}, market_open=True)
    closed_ctx = build_agent_context(["AAPL"], {}, market_open=False)
    unknown_ctx = build_agent_context(["AAPL"], {})

    assert open_ctx["market_status"] == "OPEN"
    assert closed_ctx["market_status"] == "CLOSED"
    assert unknown_ctx["market_status"] == "UNKNOWN"


# --- prompt ---


def test_s001_holds_unless_account_data_is_live_and_sells_only_owned():
    prompt = get_system_prompt("S-001")

    assert 'account.data_status` is not "LIVE"' in prompt
    assert "output HOLD" in prompt
    assert "appears in `positions`" in prompt
    assert "open_orders" in prompt


def test_s001_revision_was_bumped_for_the_new_rules():
    assert get_prompt_definition("S-001").revision == "1.2.0"


# --- reading the live account ---


class _FakeAlpaca:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_account(self):
        return SimpleNamespace(model_dump=lambda mode="json": LIVE_ACCOUNT)

    async def get_positions(self):
        return []

    async def get_orders(self, status="open"):
        return []


def _db_with_connected_account():
    row = SimpleNamespace(is_connected=True, is_paper=True, user_id=uuid.uuid4())
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row))
    )


def _patch_broker(monkeypatch, clock):
    monkeypatch.setattr(orchestrator, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(
        orchestrator,
        "get_decrypted_alpaca_credentials",
        AsyncMock(return_value=("key", "secret")),
    )
    monkeypatch.setattr(orchestrator, "fetch_market_clock_for", clock)


@pytest.mark.asyncio
async def test_portfolio_context_includes_whether_the_market_is_open(monkeypatch):
    _patch_broker(monkeypatch, AsyncMock(return_value=SimpleNamespace(is_open=True)))
    agent = SimpleNamespace(id=uuid.uuid4(), account_id=uuid.uuid4())

    portfolio = await _load_portfolio_context(agent, _db_with_connected_account())

    assert portfolio["status"] == "LIVE"
    assert portfolio["market_open"] is True


@pytest.mark.asyncio
async def test_a_failing_market_clock_does_not_make_the_portfolio_unavailable(monkeypatch):
    _patch_broker(monkeypatch, AsyncMock(side_effect=ConnectionError("clock down")))
    agent = SimpleNamespace(id=uuid.uuid4(), account_id=uuid.uuid4())

    portfolio = await _load_portfolio_context(agent, _db_with_connected_account())

    assert portfolio["status"] == "LIVE"  # the account data is still real
    assert portfolio["market_open"] is None


# --- the orchestrator hands it to the model ---

HOLD_ANSWER = json.dumps(
    {"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "No clear signal."}
)


def _cycle_inputs():
    agent = SimpleNamespace(
        id=uuid.uuid4(), watchlist=["AAPL"], strategy_version="S-001", approval_mode=None
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
    llm.generate = AsyncMock(return_value=HOLD_ANSWER)
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    return agent, db, llm, collector


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market_open", "expected"),
    [(True, "OPEN"), (False, "CLOSED"), (None, "UNKNOWN")],
)
async def test_the_model_is_told_whether_the_market_is_open(market_open, expected):
    agent, db, llm, collector = _cycle_inputs()
    portfolio = {
        "account": LIVE_ACCOUNT,
        "positions": [],
        "open_orders": [],
        "status": "LIVE",
        "market_open": market_open,
    }

    await run_agent_cycle(
        agent.id, db, llm_client=llm, collector=collector, portfolio_context=portfolio
    )

    context = json.loads(llm.generate.await_args.args[0])
    assert context["market_status"] == expected
    assert context["account"]["data_status"] == "LIVE"