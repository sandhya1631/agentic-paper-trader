"""Per-agent Ollama request timeout and model keep-alive (#203).

Server defaults come from Settings (OLLAMA_TIMEOUT_SECONDS=180,
OLLAMA_KEEP_ALIVE_MINUTES=10); an operator can override both per agent through
PATCH /agent/config. Every Ollama call of that agent's cycles (first try and self-repair,
manual and sweep) sends the agent's keep_alive and uses its timeout. Ollama's HTTP
boundary is faked with a mock transport that records each request and the timeout the
client was built with.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select
from test_agent_config import _auth, _operator, _register_and_login, _seed_agent_for

from app.accounts import llm_keys
from app.agent import orchestrator
from app.agent.orchestrator import run_agent_cycle, run_watchlist_cycles
from app.core.config import Settings, get_settings
from app.core.constants import LLM_KEY_SOURCE_SERVER, LLM_KEY_SOURCE_USER
from app.db.enums import UserRole
from app.db.models import AgentConfig, AuditEvent, TradingAccount, User
from app.llm import ollama_client as ollama_module
from app.llm.ollama_client import OllamaClient

HOLD = {"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "No setup."}


class _OllamaRecorder:
    """Stands in for Ollama's /api/chat: records payloads and client timeouts."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.payloads: list[dict] = []
        self.timeouts: list[float | None] = []
        self._replies = list(replies or [])

    def install(self, monkeypatch) -> None:
        real_client = httpx.AsyncClient

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.payloads.append(payload)
            ctx = json.loads(payload["messages"][-1]["content"]) if self._json(payload) else {}
            reply = (
                self._replies.pop(0)
                if self._replies
                else json.dumps({**HOLD, "symbol": ctx.get("decision_target", "AAPL")})
            )
            return httpx.Response(200, json={"message": {"content": reply}})

        def make_client(*args, **kwargs):
            timeout = kwargs.get("timeout")
            self.timeouts.append(timeout.read if isinstance(timeout, httpx.Timeout) else timeout)
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(ollama_module.httpx, "AsyncClient", make_client)

    @staticmethod
    def _json(payload: dict) -> bool:
        try:
            json.loads(payload["messages"][-1]["content"])
            return True
        except ValueError:
            return False


def _server_ollama(monkeypatch, **settings_overrides) -> Settings:
    """The cached server-wide client is an OllamaClient built from these Settings."""
    settings = Settings(_env_file=None, llm_provider="ollama", **settings_overrides)
    base = OllamaClient(
        settings.ollama_base_url,
        settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        keep_alive_minutes=settings.ollama_keep_alive_minutes,
    )
    monkeypatch.setattr(orchestrator, "get_llm_client", lambda: base)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: settings)
    return settings


def _collector() -> MagicMock:
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={})
    return collector


async def _agent_with(agent_id: uuid.UUID, **values) -> None:
    async with TestSessionLocal() as db:
        agent = await db.get(AgentConfig, agent_id)
        for name, value in values.items():
            setattr(agent, name, value)
        await db.commit()


# --- Settings defaults ---------------------------------------------------------------


def test_server_defaults_are_180_seconds_and_10_minutes():
    settings = Settings(_env_file=None)
    assert (settings.ollama_timeout_seconds, settings.ollama_keep_alive_minutes) == (180, 10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ollama_timeout_seconds": 29},
        {"ollama_timeout_seconds": 901},
        {"ollama_keep_alive_minutes": -2},
        {"ollama_keep_alive_minutes": 241},
    ],
)
def test_server_defaults_are_bounded(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **overrides)


# --- GET / PATCH /agent/config --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_the_effective_values_and_the_server_defaults(client: AsyncClient):
    email = f"ka-get-{uuid.uuid4().hex[:6]}@example.com"
    token = await _register_and_login(client, email)
    await _seed_agent_for(email)

    body = (await client.get("/agent/config", headers=_auth(token))).json()

    assert body["llm_timeout_seconds"] == 180
    assert body["llm_keep_alive_minutes"] == 10
    assert body["llm_timeout_default_seconds"] == 180
    assert body["llm_keep_alive_default_minutes"] == 10


@pytest.mark.asyncio
async def test_patch_sets_both_and_audits_old_to_new(client: AsyncClient):
    email = f"ka-patch-{uuid.uuid4().hex[:6]}@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    response = await client.patch(
        "/agent/config",
        headers=_auth(token),
        json={"llm_timeout_seconds": 300, "llm_keep_alive_minutes": -1},
    )

    assert response.status_code == 200, response.text
    assert (response.json()["llm_timeout_seconds"], response.json()["llm_keep_alive_minutes"]) == (
        300,
        -1,
    )
    async with TestSessionLocal() as db:
        agent = await db.get(AgentConfig, agent_id)
        assert (agent.llm_timeout_seconds, agent.llm_keep_alive_minutes) == (300, -1)
        events = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.event_type == "AGENT_CONFIG_UPDATED")
            )
        ).scalars().all()
    assert [e.details["changes"] for e in events] == [
        {
            "llm_timeout_seconds": {"from": 180, "to": 300},
            "llm_keep_alive_minutes": {"from": 10, "to": -1},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"llm_timeout_seconds": 29},
        {"llm_timeout_seconds": 901},
        {"llm_keep_alive_minutes": -2},
        {"llm_keep_alive_minutes": 241},
        {"llm_timeout_seconds": None},
        {"llm_timeout_seconds": "fast"},
        {"llm_keep_alive_minutes": 10, "ollama_base_url": "http://elsewhere"},
    ],
)
async def test_patch_rejects_out_of_range_null_and_extra_fields(client: AsyncClient, payload):
    email = f"ka-bad-{uuid.uuid4().hex[:6]}@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    response = await client.patch("/agent/config", headers=_auth(token), json=payload)

    assert response.status_code == 422
    async with TestSessionLocal() as db:
        agent = await db.get(AgentConfig, agent_id)
        assert (agent.llm_timeout_seconds, agent.llm_keep_alive_minutes) == (None, None)


@pytest.mark.asyncio
async def test_setting_the_default_again_follows_later_default_changes(
    client: AsyncClient, monkeypatch
):
    email = f"ka-reset-{uuid.uuid4().hex[:6]}@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    await client.patch("/agent/config", headers=_auth(token), json={"llm_timeout_seconds": 300})
    await client.patch("/agent/config", headers=_auth(token), json={"llm_timeout_seconds": 180})

    async with TestSessionLocal() as db:
        assert (await db.get(AgentConfig, agent_id)).llm_timeout_seconds is None
    # An admin raises the server default: the agent follows it.
    raised = Settings(_env_file=None, ollama_timeout_seconds=420)
    monkeypatch.setattr("app.agent.router.get_settings", lambda: raised)
    body = (await client.get("/agent/config", headers=_auth(token))).json()
    assert body["llm_timeout_seconds"] == 420


@pytest.mark.asyncio
async def test_an_override_equal_to_a_new_default_can_be_cleared(
    client: AsyncClient, monkeypatch
):
    email = f"ka-clear-{uuid.uuid4().hex[:6]}@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)
    await client.patch("/agent/config", headers=_auth(token), json={"llm_timeout_seconds": 300})
    # An admin raises the server default to the agent's value: effective stays 300.
    raised = Settings(_env_file=None, ollama_timeout_seconds=300)
    monkeypatch.setattr("app.agent.router.get_settings", lambda: raised)

    response = await client.patch(
        "/agent/config", headers=_auth(token), json={"llm_timeout_seconds": 300}
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        assert (await db.get(AgentConfig, agent_id)).llm_timeout_seconds is None


@pytest.mark.asyncio
async def test_an_unchanged_value_beside_another_change_is_not_stored(client: AsyncClient):
    email = f"ka-same-{uuid.uuid4().hex[:6]}@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    response = await client.patch(
        "/agent/config",
        headers=_auth(token),
        json={"approval_mode": "OBSERVE_ONLY", "llm_timeout_seconds": 180},
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        agent = await db.get(AgentConfig, agent_id)
        assert agent.llm_timeout_seconds is None
        event = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.event_type == "AGENT_CONFIG_UPDATED")
            )
        ).scalar_one()
    assert set(event.details["changes"]) == {"approval_mode"}


@pytest.mark.asyncio
async def test_a_viewer_cannot_change_them(client: AsyncClient):
    email = f"ka-viewer-{uuid.uuid4().hex[:6]}@example.com"
    token = await _register_and_login(client, email)  # VIEWER
    await _seed_agent_for(email)

    response = await client.patch(
        "/agent/config", headers=_auth(token), json={"llm_timeout_seconds": 300}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_meta_constants_expose_the_bounds(client: AsyncClient):
    body = (await client.get("/meta/constants")).json()
    assert body["llm_timeout_seconds_range"] == [30, 900]
    assert body["llm_keep_alive_minutes_range"] == [-1, 240]


# --- The Ollama request ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("keep_alive", "sent"), [(10, "10m"), (0, "0m"), (-1, -1)])
async def test_payload_carries_keep_alive_and_the_request_uses_the_timeout(
    monkeypatch, keep_alive, sent
):
    recorder = _OllamaRecorder(replies=["{}"])
    recorder.install(monkeypatch)
    client = OllamaClient(
        "http://localhost:11434", "llama3.1", timeout_seconds=42, keep_alive_minutes=keep_alive
    )

    await client.generate("ctx", system="sys")

    assert recorder.payloads[0]["keep_alive"] == sent
    assert recorder.timeouts == [42]


@pytest.mark.asyncio
async def test_the_timeout_bounds_the_answer_and_connecting_gets_10_seconds(monkeypatch):
    seen: list[httpx.Timeout] = []
    real_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        seen.append(kwargs["timeout"])
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"content": "{}"}})
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ollama_module.httpx, "AsyncClient", make_client)

    await OllamaClient("http://localhost:11434", "llama3.1", timeout_seconds=600).generate("x")

    assert (seen[0].read, seen[0].connect) == (600, 10.0)


@pytest.mark.asyncio
async def test_no_keep_alive_is_sent_when_unset(monkeypatch):
    recorder = _OllamaRecorder(replies=["{}"])
    recorder.install(monkeypatch)

    await OllamaClient("http://localhost:11434", "llama3.1").generate("ctx")

    assert "keep_alive" not in recorder.payloads[0]
    assert recorder.timeouts == [180.0]


def test_per_agent_copy_never_mutates_the_cached_client():
    base = OllamaClient("http://localhost:11434", "llama3.1", timeout_seconds=180,
                        keep_alive_minutes=10)
    derived = base.with_request_options(timeout_seconds=300, keep_alive_minutes=-1)
    assert derived is not base
    assert (base._timeout_seconds, base._keep_alive_minutes) == (180, 10)
    assert (derived._timeout_seconds, derived._keep_alive_minutes) == (300, -1)


# --- Cycles use the agent's values -----------------------------------------------------


async def _seed_operator_agent(watchlist: list[str]) -> uuid.UUID:
    email = f"ka-cycle-{uuid.uuid4().hex[:6]}@example.com"
    async with TestSessionLocal() as db:
        db.add(User(email=email, password_hash="x", role=UserRole.OPERATOR))
        await db.commit()
    return await _seed_agent_for(email, watchlist=watchlist)


@pytest.mark.asyncio
async def test_a_manual_cycle_and_its_repair_call_use_the_agents_values(
    db_session, monkeypatch
):
    _server_ollama(monkeypatch)
    recorder = _OllamaRecorder(replies=["not json", json.dumps(HOLD)])
    recorder.install(monkeypatch)
    agent_id = await _seed_operator_agent(["AAPL"])
    await _agent_with(agent_id, llm_timeout_seconds=300, llm_keep_alive_minutes=30)

    async with TestSessionLocal() as db:
        cycle = await run_agent_cycle(agent_id, db, collector=_collector(), account_data=None)

    assert cycle.status.value == "HOLD"
    assert len(recorder.payloads) == 2  # first try + self-repair
    assert [p["keep_alive"] for p in recorder.payloads] == ["30m", "30m"]
    assert recorder.timeouts == [300, 300]
    # The cycle's audit snapshot records the options it used.
    observability = cycle.snapshot_json["observability"]
    assert (observability["llm_timeout_seconds"], observability["llm_keep_alive_minutes"]) == (
        300,
        30,
    )


@pytest.mark.asyncio
async def test_an_agent_without_overrides_follows_the_server_defaults(db_session, monkeypatch):
    _server_ollama(monkeypatch, ollama_timeout_seconds=240, ollama_keep_alive_minutes=15)
    recorder = _OllamaRecorder()
    recorder.install(monkeypatch)
    agent_id = await _seed_operator_agent(["AAPL"])

    async with TestSessionLocal() as db:
        await run_agent_cycle(agent_id, db, collector=_collector(), account_data=None)

    assert [p["keep_alive"] for p in recorder.payloads] == ["15m"]
    assert recorder.timeouts == [240]


@pytest.mark.asyncio
async def test_a_sweep_uses_the_agents_values_for_every_symbol(db_session, monkeypatch):
    _server_ollama(monkeypatch)
    recorder = _OllamaRecorder()
    recorder.install(monkeypatch)
    agent_id = await _seed_operator_agent(["AAPL", "NVDA"])
    await _agent_with(agent_id, llm_timeout_seconds=600, llm_keep_alive_minutes=-1)

    async with TestSessionLocal() as db:
        cycles = await run_watchlist_cycles(agent_id, db, collector=_collector())

    assert len(cycles) == 2
    assert [p["keep_alive"] for p in recorder.payloads] == [-1, -1]
    assert recorder.timeouts == [600, 600]


@pytest.mark.asyncio
async def test_other_providers_get_the_cached_client_unchanged(db_session, monkeypatch):
    cached = MagicMock()
    cached.generate = AsyncMock(return_value=json.dumps(HOLD))
    monkeypatch.setattr(orchestrator, "get_llm_client", lambda: cached)
    agent_id = await _seed_operator_agent(["AAPL"])
    await _agent_with(agent_id, llm_timeout_seconds=300, llm_keep_alive_minutes=30)

    async with TestSessionLocal() as db:
        await run_agent_cycle(agent_id, db, collector=_collector(), account_data=None)

    cached.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_server_key_fallback_still_applies_the_agents_values(db_session, monkeypatch):
    """#191 x #203: an owner with no AI key of their own falls back to the server's
    Ollama client, which still carries this agent's timeout and keep-alive. Once the owner
    connects a key, that client serves the cycle and the Ollama options don't apply."""
    _server_ollama(monkeypatch)
    # The resolver reads the real Settings: no user key -> the server's (Ollama) LLM.
    monkeypatch.setattr(get_settings(), "llm_provider", "ollama")
    monkeypatch.setattr(get_settings(), "llm_server_key_fallback", True)
    recorder = _OllamaRecorder()
    recorder.install(monkeypatch)
    agent_id = await _seed_operator_agent(["AAPL"])
    await _agent_with(agent_id, llm_timeout_seconds=420, llm_keep_alive_minutes=-1)

    async with TestSessionLocal() as db:
        served = await run_agent_cycle(agent_id, db, collector=_collector(), account_data=None)

    assert served.status.value == "HOLD"
    assert [p["keep_alive"] for p in recorder.payloads] == [-1]
    assert recorder.timeouts == [420]
    observability = served.snapshot_json["observability"]
    assert observability["llm_key_source"] == LLM_KEY_SOURCE_SERVER
    assert observability["llm_provider"] == "ollama"
    assert (observability["llm_timeout_seconds"], observability["llm_keep_alive_minutes"]) == (
        420,
        -1,
    )

    # The owner connects their own key: it serves the next cycle; Ollama isn't called.
    user_client = MagicMock()
    user_client.generate = AsyncMock(return_value=json.dumps(HOLD))
    monkeypatch.setattr(llm_keys, "create_user_llm_client", lambda *_args: user_client)
    async with TestSessionLocal() as db:
        owner_id = (
            await db.execute(
                select(TradingAccount.user_id)
                .join(AgentConfig, AgentConfig.account_id == TradingAccount.id)
                .where(AgentConfig.id == agent_id)
            )
        ).scalar_one()
        await llm_keys.save_llm_credential(db, owner_id, "openai", "sk-test-not-a-real-key")
        await db.commit()

    async with TestSessionLocal() as db:
        own_key = await run_agent_cycle(agent_id, db, collector=_collector(), account_data=None)

    user_client.generate.assert_awaited_once()
    assert len(recorder.payloads) == 1  # still just the first cycle's Ollama call
    observability = own_key.snapshot_json["observability"]
    assert observability["llm_key_source"] == LLM_KEY_SOURCE_USER
    assert "llm_timeout_seconds" not in observability
    assert "llm_keep_alive_minutes" not in observability
