"""The agent's context shows the REAL paper account (Feature #64 / Story #66).

Before: build_agent_context was never given the account, so the model always saw
invented defaults (cash $100,000, no positions), which made SELL impossible and put
fake numbers in its reasoning. Now the live account is fetched and passed in, and when
it cannot be read the context says UNAVAILABLE (and the prompt says HOLD).
"""

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import live_context, orchestrator
from app.agent.context import build_agent_context
from app.agent.live_context import LiveAccountData, fetch_live_account_data
from app.agent.orchestrator import run_agent_cycle
from app.agent.prompt import get_system_prompt

REAL_ACCOUNT = {
    "cash": "52000.50",
    "equity": "98000.25",
    "buying_power": "104000.00",
    "daily_pnl": "-120.25",
}
REAL_POSITIONS = [
    {"symbol": "AAPL", "qty": "5", "market_value": "1000", "unrealized_pnl": "25.5"}
]
REAL_ORDERS = [{"id": "o1", "symbol": "NVDA", "side": "buy", "qty": "2", "status": "new"}]


def _live(market_open: bool | None = True) -> LiveAccountData:
    return LiveAccountData(
        account=REAL_ACCOUNT,
        positions=REAL_POSITIONS,
        open_orders=REAL_ORDERS,
        market_open=market_open,
    )


# --- the context builder ---


def test_context_shows_the_real_account_positions_orders_and_market_status():
    context = build_agent_context(
        ["AAPL"],
        {"AAPL": []},
        account_info=REAL_ACCOUNT,
        positions=REAL_POSITIONS,
        open_orders=REAL_ORDERS,
        market_open=True,
    )

    assert context["account_data"] == "LIVE"
    assert context["account"] == REAL_ACCOUNT
    assert context["positions"][0]["symbol"] == "AAPL"
    assert context["positions"][0]["qty"] == "5"
    assert context["open_orders"][0]["symbol"] == "NVDA"
    assert context["market_status"] == "OPEN"


def test_context_never_invents_numbers_when_the_account_is_unavailable():
    context = build_agent_context(["AAPL"], {"AAPL": []})

    assert context["account_data"] == "UNAVAILABLE"
    assert context["account"] == {"status": "UNAVAILABLE"}
    assert context["positions"] == []
    assert context["market_status"] == "UNKNOWN"
    text = json.dumps(context)
    assert "100000" not in text  # the old made-up cash and equity
    assert "400000" not in text  # the old made-up buying power


def test_market_status_closed_and_unknown():
    closed = build_agent_context(["AAPL"], {}, account_info=REAL_ACCOUNT, market_open=False)
    unknown = build_agent_context(["AAPL"], {}, account_info=REAL_ACCOUNT, market_open=None)

    assert closed["market_status"] == "CLOSED"
    assert unknown["market_status"] == "UNKNOWN"


def test_context_never_contains_credentials():
    context = build_agent_context(
        ["AAPL"],
        {},
        account_info={**REAL_ACCOUNT, "api_key": "SECRET-KEY", "api_secret": "SECRET"},
        market_open=True,
    )

    assert "SECRET" not in json.dumps(context)


# --- the prompt ---


def test_prompt_tells_the_model_to_hold_when_the_account_is_unavailable():
    prompt = get_system_prompt("S-001")

    assert "account_data" in prompt
    assert '"UNAVAILABLE"' in prompt
    assert "output HOLD" in prompt
    assert "appears in `positions`" in prompt  # SELL only what is owned


# --- the live fetch ---


class _FakeAlpaca:
    """Stands in for AlpacaClient as an async context manager."""

    calls: list[str] = []

    def __init__(self, **_kwargs):
        self.base_url = "https://paper-api.alpaca.markets"
        self.headers: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_account(self):
        self.calls.append("account")
        return SimpleNamespace(
            cash=Decimal("52000.50"),
            equity=Decimal("98000.25"),
            buying_power=Decimal("104000.00"),
            daily_pnl=Decimal("-120.25"),
        )

    async def get_positions(self):
        self.calls.append("positions")
        return [
            SimpleNamespace(
                symbol="AAPL",
                qty=Decimal("5"),
                market_value=Decimal("1000"),
                unrealized_pl=Decimal("25.5"),
            )
        ]

    async def get_orders(self, status="open"):
        self.calls.append(f"orders:{status}")
        return [
            SimpleNamespace(id="o1", symbol="NVDA", side="buy", qty=Decimal("2"), status="new")
        ]


def _db_with_account(**overrides):
    row = SimpleNamespace(is_connected=True, is_paper=True, user_id=uuid.uuid4(), **overrides)
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row))
    )


def _agent():
    return SimpleNamespace(account_id=uuid.uuid4())


def _patch_broker(monkeypatch, *, market_open: bool = True):
    _FakeAlpaca.calls = []
    monkeypatch.setattr(live_context, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(
        live_context,
        "get_decrypted_alpaca_credentials",
        AsyncMock(return_value=("key", "secret")),
    )
    monkeypatch.setattr(
        live_context,
        "fetch_market_clock_for",
        AsyncMock(return_value=SimpleNamespace(is_open=market_open)),
    )


@pytest.mark.asyncio
async def test_fetch_maps_the_broker_account_into_plain_sanitized_values(monkeypatch):
    _patch_broker(monkeypatch)
    agent = SimpleNamespace(account_id=uuid.uuid4())

    data = await fetch_live_account_data(_db_with_account(), agent)

    assert data is not None
    assert data.account == REAL_ACCOUNT
    assert data.positions == REAL_POSITIONS
    assert data.open_orders == REAL_ORDERS
    assert data.market_open is True
    assert _FakeAlpaca.calls == ["account", "positions", "orders:open"]
    assert "key" not in json.dumps(data.account)


@pytest.mark.asyncio
async def test_fetch_reports_a_closed_market(monkeypatch):
    _patch_broker(monkeypatch, market_open=False)

    data = await fetch_live_account_data(_db_with_account(), _agent())

    assert data is not None
    assert data.market_open is False


@pytest.mark.asyncio
async def test_fetch_returns_none_without_a_connected_paper_account(monkeypatch):
    _patch_broker(monkeypatch)
    agent = SimpleNamespace(account_id=uuid.uuid4())

    no_row = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    )
    assert await fetch_live_account_data(no_row, agent) is None

    disconnected = SimpleNamespace(
        is_connected=False, is_paper=True, user_id=uuid.uuid4()
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: disconnected))
    )
    assert await fetch_live_account_data(db, agent) is None
    assert _FakeAlpaca.calls == []  # the broker was never called


@pytest.mark.asyncio
async def test_fetch_returns_none_and_never_raises_when_credentials_fail(monkeypatch):
    _patch_broker(monkeypatch)
    monkeypatch.setattr(
        live_context,
        "get_decrypted_alpaca_credentials",
        AsyncMock(side_effect=RuntimeError("could not decrypt")),
    )

    data = await fetch_live_account_data(_db_with_account(), _agent())

    assert data is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_the_broker_fails(monkeypatch):
    _patch_broker(monkeypatch)
    broken = AsyncMock(side_effect=ConnectionError("down"))
    monkeypatch.setattr(_FakeAlpaca, "get_account", broken)

    data = await fetch_live_account_data(_db_with_account(), _agent())

    assert data is None


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


async def _run_and_capture_prompt(monkeypatch, fetched):
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", AsyncMock(return_value=fetched))
    agent, db, llm, collector = _cycle_inputs()
    await run_agent_cycle(agent.id, db, llm_client=llm, collector=collector)
    return json.loads(llm.generate.await_args.args[0])


@pytest.mark.asyncio
async def test_the_model_receives_the_real_account(monkeypatch):
    context = await _run_and_capture_prompt(monkeypatch, _live())

    assert context["account_data"] == "LIVE"
    assert context["account"]["cash"] == "52000.50"
    assert [p["symbol"] for p in context["positions"]] == ["AAPL"]
    assert context["open_orders"][0]["symbol"] == "NVDA"
    assert context["market_status"] == "OPEN"


@pytest.mark.asyncio
async def test_the_model_is_told_when_the_account_could_not_be_read(monkeypatch):
    context = await _run_and_capture_prompt(monkeypatch, None)

    assert context["account_data"] == "UNAVAILABLE"
    assert context["account"] == {"status": "UNAVAILABLE"}
    assert "100000" not in json.dumps(context)


@pytest.mark.asyncio
async def test_caller_provided_data_is_used_without_another_fetch(monkeypatch):
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", fetch)
    agent, db, llm, collector = _cycle_inputs()

    await run_agent_cycle(
        agent.id, db, llm_client=llm, collector=collector, account_data=_live(market_open=False)
    )

    fetch.assert_not_awaited()
    context = json.loads(llm.generate.await_args.args[0])
    assert context["account"]["cash"] == "52000.50"
    assert context["market_status"] == "CLOSED"