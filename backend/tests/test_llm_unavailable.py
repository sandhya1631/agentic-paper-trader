"""An unavailable LLM is a 503 / FAILED-LLM_UNAVAILABLE, never a 404 or a generic 500 (#160).

Covers misconfiguration when the client is built (missing key, unknown provider) and
provider failures from generate() (OpenAI/Claude SDK errors, Ollama timeouts, connect
and HTTP errors), for manual cycles, the self-repair call, sweeps and replay. Canary
keys and bodies prove no secret or provider response reaches a response, log or row.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select, text

from app.agent import orchestrator
from app.agent.orchestrator import run_agent_cycle, run_watchlist_cycles
from app.core.config import Settings
from app.core.constants import LLM_UNAVAILABLE
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.llm import factory
from app.llm import ollama_client as ollama_module
from app.llm.base import LLMUnavailableError
from app.llm.factory import create_llm_client, get_llm_client
from app.llm.ollama_client import OllamaClient
from app.main import app

PASSWORD = "S3cure-Password!"
CANARY_KEY = "sk-canary-LLM160-0123456789abcdefX4LM"
# OpenAI's 401 quotes a key as its first 8 + last 4 characters.
CANARY_FRAGMENTS = (CANARY_KEY, CANARY_KEY[:8] + "*", "X4LM", "canary-LLM160")
SECRET_BODY = "provider-body-SECRET-160"


@pytest.fixture(autouse=True)
def _fresh_llm_client_cache():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


def _use_settings(monkeypatch, **overrides) -> Settings:
    settings = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    return settings


def _offline_cycle(monkeypatch) -> None:
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    monkeypatch.setattr(orchestrator, "create_market_data_collector", lambda: collector)
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", AsyncMock(return_value=None))


async def _operator_with_agent(
    client: AsyncClient, *, watchlist=("AAPL",)
) -> tuple[dict, uuid.UUID]:
    email = f"llm160-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = UserRole.OPERATOR
        agent_id = await _seed_agent(db, user, list(watchlist))
    return auth, agent_id


async def _seed_agent(db, user: User, watchlist: list[str]) -> uuid.UUID:
    account = TradingAccount(user_id=user.id, provider="ALPACA", encrypted_api_key="k",
                             encrypted_api_secret="s", is_paper=True, is_connected=True)
    db.add(account)
    await db.flush()
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db.add(policy_set)
    await db.flush()
    agent = AgentConfig(account_id=account.id, policy_set_id=policy_set.id, name="LLM agent",
                        watchlist=watchlist, strategy_version="S-001",
                        approval_mode=ApprovalMode.REQUIRED, enabled=True,
                        model_provider="ollama", model_name="llama3.1")
    db.add(agent)
    await db.commit()
    return agent.id


async def _only_cycle(agent_id: uuid.UUID) -> AgentCycle:
    async with TestSessionLocal() as db:
        return (
            await db.execute(select(AgentCycle).where(AgentCycle.agent_id == agent_id))
        ).scalar_one()


async def _all_stored_text() -> str:
    """Every agent_cycles row as raw text, to search for leaked secrets."""
    async with TestSessionLocal() as db:
        rows = await db.execute(text("SELECT row_to_json(c)::text FROM agent_cycles c"))
        return "\n".join(row[0] for row in rows)


def _assert_no_secret(*texts: str, url_allowed: bool = False) -> None:
    """No key fragment or provider body; no Ollama URL either, unless it's a server log
    (httpx's own INFO line names the configured URL, which is config, not a secret)."""
    fragments = (*CANARY_FRAGMENTS, SECRET_BODY) + (() if url_allowed else ("localhost:11434",))
    for blob in texts:
        for fragment in fragments:
            assert fragment not in blob, fragment


# --- The issue's tests --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"llm_provider": "anthropic", "anthropic_api_key": None},
         "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"),
        ({"llm_provider": "openai", "openai_api_key": None},
         "OPENAI_API_KEY is required when LLM_PROVIDER=openai"),
        ({"llm_provider": "bogus"}, "Unsupported LLM_PROVIDER 'bogus'"),
    ],
)
async def test_manual_cycle_llm_misconfigured_returns_503(
    client: AsyncClient, monkeypatch, overrides, message
):
    auth, agent_id = await _operator_with_agent(client)
    _use_settings(monkeypatch, **overrides)
    _offline_cycle(monkeypatch)

    response = await client.post(f"/agent/cycles/run?agent_id={agent_id}", headers=auth)

    assert response.status_code == 503, response.text
    assert response.json()["detail"].startswith(f"LLM unavailable: {message}")
    cycle = await _only_cycle(agent_id)
    assert (cycle.status, cycle.failure_code) == (CycleStatus.FAILED, LLM_UNAVAILABLE)
    assert cycle.failure_message.startswith(message)
    assert cycle.completed_at is not None


@pytest.mark.asyncio
async def test_manual_cycle_provider_error_returns_503(client: AsyncClient, monkeypatch, caplog):
    auth, agent_id = await _operator_with_agent(client)
    _use_settings(monkeypatch, llm_provider="openai", openai_api_key=CANARY_KEY)
    _offline_cycle(monkeypatch)
    # Shaped like OpenAI's real 401: the message and body quote the masked key.
    masked = f"Incorrect API key provided: {CANARY_KEY[:8]}****X4LM. {SECRET_BODY}"
    error = openai.AuthenticationError(
        masked,
        response=MagicMock(status_code=401, headers={"x-request-id": "req_160"}),
        body={"message": masked, "type": "invalid_request_error", "code": "invalid_api_key"},
    )
    sdk = MagicMock()
    sdk.return_value.__aenter__.return_value = sdk.return_value
    sdk.return_value.__aexit__.return_value = False
    sdk.return_value.chat.completions.create = AsyncMock(side_effect=error)

    with caplog.at_level(logging.DEBUG), patch("app.llm.openai_client.AsyncOpenAI", sdk):
        response = await client.post(f"/agent/cycles/run?agent_id={agent_id}", headers=auth)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "LLM unavailable: OpenAI API error: AuthenticationError (HTTP 401)"
    }
    cycle = await _only_cycle(agent_id)
    assert (cycle.failure_code, cycle.failure_message) == (
        LLM_UNAVAILABLE,
        "OpenAI API error: AuthenticationError (HTTP 401)",
    )
    _assert_no_secret(response.text, await _all_stored_text())
    _assert_no_secret(caplog.text, url_allowed=True)


@pytest.mark.asyncio
async def test_unknown_agent_still_returns_404(client: AsyncClient, monkeypatch):
    auth, _agent_id = await _operator_with_agent(client)
    _use_settings(monkeypatch, llm_provider="anthropic", anthropic_api_key=None)

    response = await client.post(f"/agent/cycles/run?agent_id={uuid.uuid4()}", headers=auth)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sweep_records_llm_unavailable_per_symbol(db_session):
    async with TestSessionLocal() as db:
        user = User(email=f"sweep160-{uuid.uuid4().hex[:6]}@example.com", password_hash="x",
                    role=UserRole.OPERATOR)
        db.add(user)
        await db.flush()
        agent_id = await _seed_agent(db, user, ["AAPL", "NVDA", "SPY"])
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=LLMUnavailableError("Ollama API error: ConnectError"))
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={})

    async with TestSessionLocal() as db:
        # Every symbol failed, so the sweep re-raises and the tick counts as FAILED.
        with pytest.raises(LLMUnavailableError):
            await run_watchlist_cycles(
                agent_id, db, trigger=CycleTrigger.SCHEDULED, llm_client=llm,
                collector=collector,
            )

    async with TestSessionLocal() as db:
        cycles = (
            await db.execute(select(AgentCycle).where(AgentCycle.agent_id == agent_id))
        ).scalars().all()
    assert len(cycles) == 3
    assert {(c.status, c.failure_code, c.failure_message) for c in cycles} == {
        (CycleStatus.FAILED, LLM_UNAVAILABLE, "Ollama API error: ConnectError")
    }


# --- Beyond the issue's list --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_self_repair_call_is_llm_unavailable(db_session):
    async with TestSessionLocal() as db:
        user = User(email=f"repair160-{uuid.uuid4().hex[:6]}@example.com", password_hash="x",
                    role=UserRole.OPERATOR)
        db.add(user)
        await db.flush()
        agent_id = await _seed_agent(db, user, ["AAPL"])
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=["not json", LLMUnavailableError("Ollama API error: ReadTimeout")]
    )
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})

    async with TestSessionLocal() as db:
        with pytest.raises(LLMUnavailableError):
            await run_agent_cycle(
                agent_id, db, llm_client=llm, collector=collector, account_data=None
            )

    assert llm.generate.await_count == 2
    cycle = await _only_cycle(agent_id)
    assert (cycle.failure_code, cycle.failure_message) == (
        LLM_UNAVAILABLE,
        "Ollama API error: ReadTimeout",
    )
    # The evidence it had is kept: the first (invalid) answer, the prompt, the context.
    assert cycle.llm_raw_response == "not json"
    assert cycle.prompt_text
    assert cycle.snapshot_json["watchlist"] == ["AAPL"]


@pytest.mark.asyncio
async def test_manual_cycle_ollama_timeout_returns_503_with_a_readable_message(
    client: AsyncClient, monkeypatch
):
    auth, agent_id = await _operator_with_agent(client)
    _use_settings(monkeypatch, llm_provider="ollama", ollama_base_url="http://localhost:11434")
    _offline_cycle(monkeypatch)
    _ollama_transport(monkeypatch, lambda request: (_ for _ in ()).throw(
        httpx.ReadTimeout("", request=request)
    ))

    response = await client.post(f"/agent/cycles/run?agent_id={agent_id}", headers=auth)

    assert response.status_code == 503
    assert response.json()["detail"] == "LLM unavailable: Ollama API error: ReadTimeout"
    cycle = await _only_cycle(agent_id)
    assert cycle.failure_message == "Ollama API error: ReadTimeout"  # was "" before #160


def _ollama_transport(monkeypatch, handler) -> None:
    """Route OllamaClient's own httpx.AsyncClient through a mock transport."""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ollama_module.httpx, "AsyncClient", make_client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("", request=r)),
         "Ollama API error: ReadTimeout"),
        (lambda r: (_ for _ in ()).throw(
            httpx.ConnectError(f"All connection attempts failed: {r.url}", request=r)),
         "Ollama API error: ConnectError"),
        (lambda r: httpx.Response(500, json={"error": SECRET_BODY}),
         "Ollama API error: HTTPStatusError (HTTP 500)"),
        (lambda r: httpx.Response(200, text=f"<html>{SECRET_BODY}</html>"),
         "Ollama API error: JSONDecodeError"),
        (lambda r: httpx.Response(200, json=[SECRET_BODY]),
         "Ollama API error: unexpected response shape"),
        (lambda r: httpx.Response(404, json={"error": f"model not found {SECRET_BODY}"}),
         "Ollama API error: HTTPStatusError (HTTP 404). Check that OLLAMA_MODEL is pulled "
         "(ollama pull llama3.1) and that OLLAMA_BASE_URL points at Ollama"),
    ],
)
async def test_ollama_generate_failures_are_sanitized_llm_unavailable(
    monkeypatch, caplog, handler, message
):
    _ollama_transport(monkeypatch, handler)
    client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMUnavailableError) as raised:
        await client.generate("ctx", system="sys")

    assert str(raised.value) == message
    # No chained provider exception can surface the body or URL later.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or raised.value.__suppress_context__
    _assert_no_secret(str(raised.value))
    _assert_no_secret(caplog.text, url_allowed=True)


@pytest.mark.asyncio
async def test_an_invalid_ollama_url_is_llm_unavailable_without_the_url():
    client = OllamaClient(base_url="http://[bad-host-SECRET-160", model="llama3.1")

    with pytest.raises(LLMUnavailableError) as raised:
        await client.generate("ctx")

    assert str(raised.value).startswith("Ollama API error: ")
    assert "bad-host" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_cancel_during_a_hung_llm_call_is_kept(db_session):
    """The user cancels while the call hangs; the outage must not overwrite CANCELLED."""
    async with TestSessionLocal() as db:
        user = User(email=f"cancel160-{uuid.uuid4().hex[:6]}@example.com", password_hash="x",
                    role=UserRole.OPERATOR)
        db.add(user)
        await db.flush()
        agent_id = await _seed_agent(db, user, ["AAPL"])

    async def hang_then_fail(prompt_input, system=None):
        async with TestSessionLocal() as other:  # POST /agent/cycles/{id}/cancel
            cycle = (
                await other.execute(
                    select(AgentCycle).where(AgentCycle.agent_id == agent_id).with_for_update()
                )
            ).scalar_one()
            cycle.status = CycleStatus.CANCELLED
            cycle.failure_code = "MANUAL_CYCLE_CANCELLED"
            await other.commit()
        raise LLMUnavailableError("Ollama API error: ReadTimeout")

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=hang_then_fail)
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    async with TestSessionLocal() as db:
        returned = await run_agent_cycle(
            agent_id, db, llm_client=llm, collector=collector, account_data=None
        )

    assert returned.status == CycleStatus.CANCELLED
    cycle = await _only_cycle(agent_id)
    assert (cycle.status, cycle.failure_code) == (
        CycleStatus.CANCELLED,
        "MANUAL_CYCLE_CANCELLED",
    )


@pytest.mark.asyncio
async def test_other_value_errors_in_a_cycle_are_not_reported_as_not_found(
    client: AsyncClient, monkeypatch
):
    auth, agent_id = await _operator_with_agent(client)
    _offline_cycle(monkeypatch)

    def broken_context(**_kwargs):
        raise ValueError("bad value while building the context")

    monkeypatch.setattr(orchestrator, "build_agent_context", broken_context)

    response = await client.post(f"/agent/cycles/run?agent_id={agent_id}", headers=auth)

    assert response.status_code == 500  # a bug, not "not found"
    assert (await _only_cycle(agent_id)).failure_code == "UNEXPECTED_ERROR"


def test_llm_unavailable_is_not_a_value_error():
    # The cycle route maps ValueError to 404; an LLM outage must never land there.
    assert not issubclass(LLMUnavailableError, ValueError)
    with pytest.raises(LLMUnavailableError):
        create_llm_client(Settings(_env_file=None, llm_provider="bogus"))


def _replay_body() -> dict:
    now = datetime.now(UTC)
    bars = [
        {"timestamp": (now - timedelta(minutes=5 * (5 - i))).isoformat(), "open": 1,
         "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(5)
    ]
    return {"samples": [{"symbols": ["AAPL"], "bars_per_symbol": {"AAPL": bars}}]}


@pytest.mark.asyncio
async def test_replay_with_a_misconfigured_llm_returns_503(client: AsyncClient, monkeypatch):
    auth, _agent_id = await _operator_with_agent(client)
    _use_settings(monkeypatch, llm_provider="anthropic", anthropic_api_key=None)

    response = await client.post("/agent/replay", headers=auth, json=_replay_body())

    assert response.status_code == 503
    assert response.json() == {
        "detail": "LLM unavailable: ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
    }


@pytest.mark.asyncio
async def test_replay_with_a_failing_provider_returns_503(client: AsyncClient):
    auth, _agent_id = await _operator_with_agent(client)
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=LLMUnavailableError("OpenAI API error: RateLimitError (HTTP 429)")
    )
    app.dependency_overrides[get_llm_client] = lambda: llm
    try:
        response = await client.post("/agent/replay", headers=auth, json=_replay_body())
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "LLM unavailable: OpenAI API error: RateLimitError (HTTP 429)"
    )


@pytest.mark.asyncio
async def test_health_probe_still_reports_503_for_a_misconfigured_llm(
    client: AsyncClient, monkeypatch
):
    settings = _use_settings(monkeypatch, llm_provider="anthropic", anthropic_api_key=None)
    monkeypatch.setattr("app.llm.router.get_settings", lambda: settings)

    probe = await client.get("/llm/health?probe=true")

    assert probe.status_code == 503
    body = probe.json()
    assert body["reachable"] is False
    assert "ANTHROPIC_API_KEY is required" in body["detail"]
    assert json.dumps(body).count("sk-") == 0
