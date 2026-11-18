"""Users' own OpenAI / Claude keys (#191): vault, /accounts/llm, per-owner cycle clients.

Cycle tests take ``db_session`` only for its fresh tables; they open their own sessions.

Every key here is a canary. OpenAI's 401 body quotes a key's first 8 and last 4
characters, so each leak check searches for those fragments as well as the whole key.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select

from app.accounts import llm_keys
from app.accounts.credential_vault import decrypt_credential
from app.agent import orchestrator
from app.agent.orchestrator import run_agent_cycle, run_watchlist_cycles
from app.core.config import get_settings
from app.core.constants import (
    LLM_KEY_CONNECTED,
    LLM_KEY_DISCONNECTED,
    LLM_KEY_SOURCE_SERVER,
    LLM_KEY_SOURCE_USER,
    LLM_UNAVAILABLE,
)
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import (
    AgentConfig,
    AgentCycle,
    AuditEvent,
    LLMCredential,
    PolicySet,
    TradingAccount,
    User,
)
from app.llm.base import LLMClient, LLMHealth, LLMUnavailableError

PASSWORD = "S3cure-Password!"
OPENAI_SDK = "app.llm.openai_client.AsyncOpenAI"
KEY_A = "sk-CNRYaaaa-user-a-canary-key-Q7Z1"
KEY_B = "sk-ant-CNRYbbbb-user-b-canary-key-W4X8"
HOLD = {"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "No setup."}


def _fragments(key: str) -> tuple[str, ...]:
    return (key, key[:8], key[-4:])


def _assert_no_key(text: str, *keys: str) -> None:
    for key in keys:
        for fragment in _fragments(key):
            assert fragment not in text, f"key fragment {fragment!r} leaked"


# --- Fakes ---------------------------------------------------------------------------


class _FakeLLM(LLMClient):
    """A provider client that answers HOLD and remembers which key built it."""

    def __init__(self, provider: str, api_key: str | None = None, *, error=None) -> None:
        self.provider = provider
        self.api_key = api_key
        self.generate_calls = 0
        self._error = error

    async def generate(self, prompt, *, system=None, temperature=None) -> str:
        self.generate_calls += 1
        if self._error is not None:
            raise self._error
        return json.dumps(HOLD)

    async def health_check(self) -> LLMHealth:  # pragma: no cover - not used
        raise AssertionError("not used")


class _Clients:
    """Records every user-key client the resolver builds, and the server client."""

    def __init__(self, *, user_error=None) -> None:
        self.user_clients: list[_FakeLLM] = []
        self.server = _FakeLLM("server")
        self.server_builds = 0
        self._user_error = user_error

    def build_user(self, provider, api_key, settings) -> _FakeLLM:
        client = _FakeLLM(provider, api_key, error=self._user_error)
        self.user_clients.append(client)
        return client

    def build_server(self) -> _FakeLLM:
        self.server_builds += 1
        return self.server


def _install(monkeypatch, clients: _Clients) -> MagicMock:
    """Offline cycle: fake LLM clients, no bars, no broker account. Returns the collector."""
    monkeypatch.setattr(llm_keys, "create_user_llm_client", clients.build_user)
    monkeypatch.setattr(orchestrator, "get_llm_client", clients.build_server)
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": [], "MSFT": []})
    monkeypatch.setattr(orchestrator, "create_market_data_collector", lambda: collector)
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", AsyncMock(return_value=None))
    return collector


def _mock_openai_sdk(sdk_cls: MagicMock) -> MagicMock:
    sdk = sdk_cls.return_value
    sdk.__aenter__.return_value = sdk
    sdk.__aexit__.return_value = False
    return sdk


def _openai_models_ok(sdk_cls: MagicMock) -> None:
    model = get_settings().openai_model
    _mock_openai_sdk(sdk_cls).models.list = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(id=model)])
    )


def _openai_401(key: str) -> openai.AuthenticationError:
    """OpenAI's real 401: its message and body quote the key's first 8 and last 4."""
    masked = f"{key[:8]}{'*' * 12}{key[-4:]}"
    message = f"Error code: 401 - Incorrect API key provided: {masked}."
    response = MagicMock(status_code=401, headers={"x-request-id": "req_x"})
    body = {"message": message, "type": "invalid_request_error", "code": "invalid_api_key"}
    return openai.AuthenticationError(message, response=response, body=body)


# --- Setup ---------------------------------------------------------------------------


async def _login(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    email = f"llmkey-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    async with TestSessionLocal() as db:
        user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, user_id


async def _user() -> uuid.UUID:
    async with TestSessionLocal() as db:
        user = User(
            email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            role=UserRole.OPERATOR,
        )
        db.add(user)
        await db.commit()
        return user.id


async def _agent(user_id: uuid.UUID, watchlist=("AAPL",)) -> uuid.UUID:
    async with TestSessionLocal() as db:
        account = TradingAccount(user_id=user_id, provider="ALPACA", encrypted_api_key="k",
                                 encrypted_api_secret="s", is_paper=True, is_connected=True)
        db.add(account)
        policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
        db.add(policy_set)
        await db.flush()
        agent = AgentConfig(account_id=account.id, policy_set_id=policy_set.id, name="Agent",
                            watchlist=list(watchlist), strategy_version="S-001",
                            approval_mode=ApprovalMode.REQUIRED, enabled=True,
                            model_provider="ollama", model_name="llama3.1")
        db.add(agent)
        await db.commit()
        return agent.id


async def _store_key(user_id: uuid.UUID, provider: str, key: str) -> None:
    async with TestSessionLocal() as db:
        await llm_keys.save_llm_credential(db, user_id, provider, key)
        await db.commit()


async def _cycles(agent_id: uuid.UUID) -> list[AgentCycle]:
    async with TestSessionLocal() as db:
        result = await db.execute(
            select(AgentCycle)
            .where(AgentCycle.agent_id == agent_id)
            .order_by(AgentCycle.started_at)
        )
        return list(result.scalars())


async def _audit_text() -> str:
    async with TestSessionLocal() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
        return json.dumps([[e.event_type, e.entity_type, e.details] for e in events])


# --- Vault ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_encrypts_replaces_and_revokes(db_session):
    user = User(email=f"v-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add(user)
    await db_session.commit()

    first = await llm_keys.save_llm_credential(db_session, user.id, "openai", KEY_A)
    await db_session.commit()
    assert first.is_connected and first.last_verified_at is not None
    _assert_no_key(first.encrypted_api_key, KEY_A)
    assert decrypt_credential(first.encrypted_api_key) == KEY_A

    # Reconnecting replaces the provider and key on the same (only) row.
    second = await llm_keys.save_llm_credential(db_session, user.id, "anthropic", KEY_B)
    await db_session.commit()
    rows = (
        await db_session.execute(select(LLMCredential).where(LLMCredential.user_id == user.id))
    ).scalars().all()
    assert [row.id for row in rows] == [first.id] and second.id == first.id
    assert second.provider == "anthropic"
    assert decrypt_credential(second.encrypted_api_key) == KEY_B

    revoked = await llm_keys.revoke_llm_credential(db_session, user.id)
    await db_session.commit()
    assert revoked is not None
    assert revoked.is_connected is False and revoked.encrypted_api_key == ""
    assert await llm_keys.revoke_llm_credential(db_session, user.id) is None


# --- API -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_key_routes_require_auth(client: AsyncClient):
    assert (await client.post("/accounts/llm", json={"provider": "openai", "api_key": KEY_A})
            ).status_code == 401
    assert (await client.get("/accounts/llm")).status_code == 401
    assert (await client.delete("/accounts/llm")).status_code == 401


@pytest.mark.asyncio
async def test_connect_verifies_stores_encrypted_audits_and_never_echoes(client, caplog):
    auth, user_id = await _login(client)
    with patch(OPENAI_SDK) as sdk_cls, caplog.at_level(logging.DEBUG):
        _openai_models_ok(sdk_cls)
        response = await client.post(
            "/accounts/llm", headers=auth, json={"provider": "openai", "api_key": f" {KEY_A} "}
        )
        status = await client.get("/accounts/llm", headers=auth)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"provider", "is_connected", "last_verified_at"}
    assert body["provider"] == "openai" and body["is_connected"] is True
    # Verified with the user's key (stripped), through the free model listing.
    sdk_cls.assert_called_with(api_key=KEY_A, timeout=10.0, max_retries=2)
    assert status.json() == {
        "connected": True,
        "provider": "openai",
        "last_verified_at": body["last_verified_at"],
        "effective_source": LLM_KEY_SOURCE_USER,
        "effective_provider": "openai",
        "effective_model": get_settings().openai_model,
    }
    async with TestSessionLocal() as db:
        row = (
            await db.execute(select(LLMCredential).where(LLMCredential.user_id == user_id))
        ).scalar_one()
        events = (await db.execute(select(AuditEvent))).scalars().all()
    assert decrypt_credential(row.encrypted_api_key) == KEY_A
    _assert_no_key(row.encrypted_api_key, KEY_A)
    assert [(e.event_type, e.actor_id, e.entity_id, e.details) for e in events] == [
        (LLM_KEY_CONNECTED, user_id, row.id, {"provider": "openai"})
    ]
    _assert_no_key(response.text + status.text + caplog.text + await _audit_text(), KEY_A)


@pytest.mark.asyncio
async def test_connect_claude_key_uses_the_servers_claude_model(client, monkeypatch):
    auth, _user_id = await _login(client)
    from app.llm.anthropic_client import AnthropicClient

    seen: list[str] = []

    async def healthy(self) -> LLMHealth:
        seen.append(self._api_key)
        return LLMHealth("anthropic", self._model, True, True, "ok")

    monkeypatch.setattr(AnthropicClient, "health_check", healthy)
    response = await client.post(
        "/accounts/llm", headers=auth, json={"provider": "anthropic", "api_key": KEY_B}
    )
    status = (await client.get("/accounts/llm", headers=auth)).json()

    assert response.status_code == 201
    assert seen == [KEY_B]
    assert status["effective_provider"] == "anthropic"
    assert status["effective_model"] == get_settings().anthropic_model


@pytest.mark.asyncio
async def test_rejected_key_is_422_with_no_key_fragment_and_nothing_stored(client, caplog):
    auth, _user_id = await _login(client)
    with patch(OPENAI_SDK) as sdk_cls, caplog.at_level(logging.DEBUG):
        _mock_openai_sdk(sdk_cls).models.list = AsyncMock(side_effect=_openai_401(KEY_A))
        response = await client.post(
            "/accounts/llm", headers=auth, json={"provider": "openai", "api_key": KEY_A}
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "OpenAI did not accept this key, so it was not saved: "
        "OpenAI API unreachable: AuthenticationError (HTTP 401)"
    }
    _assert_no_key(response.text + caplog.text, KEY_A)
    async with TestSessionLocal() as db:
        assert (await db.execute(select(LLMCredential))).scalars().all() == []
        assert (await db.execute(select(AuditEvent))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": KEY_A},  # missing provider: FastAPI's default echoes the whole body
        {"provider": "gemini", "api_key": KEY_A},
        {"provider": "openai", "api_key": KEY_A, "extra": KEY_A},
        {"provider": "openai", "api_key": "   "},
    ],
)
async def test_invalid_body_422_never_echoes_the_key(client: AsyncClient, payload):
    auth, _user_id = await _login(client)
    response = await client.post("/accounts/llm", headers=auth, json=payload)

    assert response.status_code == 422
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
    _assert_no_key(response.text, KEY_A)


@pytest.mark.asyncio
async def test_get_reports_server_key_and_nothing_when_fallback_is_off(client, monkeypatch):
    auth, _user_id = await _login(client)
    settings = get_settings()

    fallback = (await client.get("/accounts/llm", headers=auth)).json()
    monkeypatch.setattr(settings, "llm_server_key_fallback", False)
    required = (await client.get("/accounts/llm", headers=auth)).json()

    assert fallback == {
        "connected": False,
        "provider": None,
        "last_verified_at": None,
        "effective_source": LLM_KEY_SOURCE_SERVER,
        "effective_provider": settings.llm_provider.lower(),
        "effective_model": llm_keys.configured_model(settings),
    }
    assert required["connected"] is False
    assert required["effective_source"] is None and required["effective_model"] is None


@pytest.mark.asyncio
async def test_delete_revokes_audits_and_falls_back_to_the_server_key(client: AsyncClient):
    auth, user_id = await _login(client)
    await _store_key(user_id, "openai", KEY_A)

    response = await client.delete("/accounts/llm", headers=auth)
    status = (await client.get("/accounts/llm", headers=auth)).json()
    again = await client.delete("/accounts/llm", headers=auth)

    assert response.status_code == 204
    assert status["connected"] is False and status["effective_source"] == LLM_KEY_SOURCE_SERVER
    assert again.status_code == 404
    async with TestSessionLocal() as db:
        row = (await db.execute(select(LLMCredential))).scalar_one()
        events = (await db.execute(select(AuditEvent))).scalars().all()
    assert row.is_connected is False and row.encrypted_api_key == ""
    assert [(e.event_type, e.actor_id, e.details) for e in events] == [
        (LLM_KEY_DISCONNECTED, user_id, {"provider": "openai"})
    ]


@pytest.mark.asyncio
async def test_one_users_key_is_invisible_to_another(client: AsyncClient):
    _auth_a, user_a = await _login(client)
    auth_b, _user_b = await _login(client)
    await _store_key(user_a, "openai", KEY_A)

    status_b = await client.get("/accounts/llm", headers=auth_b)
    delete_b = await client.delete("/accounts/llm", headers=auth_b)

    assert status_b.json()["connected"] is False
    assert status_b.json()["effective_source"] == LLM_KEY_SOURCE_SERVER
    assert delete_b.status_code == 404
    async with TestSessionLocal() as db:
        row = (await db.execute(select(LLMCredential))).scalar_one()
    assert row.user_id == user_a and row.is_connected
    _assert_no_key(status_b.text + delete_b.text, KEY_A)


# --- Cycles: the agent owner's key ----------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_uses_the_owners_key_and_records_it_without_the_key(
    db_session, monkeypatch, caplog
):
    clients = _Clients()
    _install(monkeypatch, clients)
    settings = get_settings()
    # The server runs Ollama with a small window; the owner's OpenAI key must win the
    # context budget too.
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_num_ctx", 4096)
    owner = await _user()
    agent_id = await _agent(owner)
    await _store_key(owner, "openai", KEY_A)

    with caplog.at_level(logging.DEBUG):
        async with TestSessionLocal() as db:
            cycle = await run_agent_cycle(agent_id, db)

    assert cycle.status == CycleStatus.HOLD
    assert [(c.provider, c.api_key, c.generate_calls) for c in clients.user_clients] == [
        ("openai", KEY_A, 1)
    ]
    assert clients.server_builds == 0
    observability = cycle.snapshot_json["observability"]
    assert observability["llm_provider"] == "openai"
    assert observability["llm_model"] == settings.openai_model
    assert observability["llm_key_source"] == LLM_KEY_SOURCE_USER
    assert observability["model_window_tokens"] == 128000
    _assert_no_key(
        json.dumps(cycle.snapshot_json) + (cycle.prompt_text or "")
        + (cycle.llm_raw_response or "") + caplog.text + await _audit_text(),
        KEY_A,
    )


@pytest.mark.asyncio
async def test_cycle_without_a_key_uses_the_server_and_fails_if_fallback_is_off(
    db_session, monkeypatch
):
    clients = _Clients()
    collector = _install(monkeypatch, clients)
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_num_ctx", 4096)
    agent_id = await _agent(await _user())

    async with TestSessionLocal() as db:
        served = await run_agent_cycle(agent_id, db)
    observability = served.snapshot_json["observability"]
    assert served.status == CycleStatus.HOLD
    assert clients.server.generate_calls == 1 and clients.user_clients == []
    assert observability["llm_key_source"] == LLM_KEY_SOURCE_SERVER
    assert observability["llm_provider"] == "ollama"
    assert observability["llm_model"] == settings.ollama_model
    assert observability["model_window_tokens"] == 4096

    monkeypatch.setattr(settings, "llm_server_key_fallback", False)
    collector.fetch_latest_bars.reset_mock()
    async with TestSessionLocal() as db:
        with pytest.raises(LLMUnavailableError):
            await run_agent_cycle(agent_id, db)
    failed = (await _cycles(agent_id))[-1]
    assert failed.status == CycleStatus.FAILED
    assert failed.failure_code == LLM_UNAVAILABLE
    assert failed.failure_message == llm_keys.NO_KEY_AND_NO_FALLBACK
    # No LLM call, and nothing else fetched either.
    assert clients.server_builds == 1 and clients.server.generate_calls == 1
    collector.fetch_latest_bars.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_user_key_fails_the_cycle_without_falling_back(db_session, monkeypatch):
    rejected = LLMUnavailableError("OpenAI API error: AuthenticationError (HTTP 401)")
    clients = _Clients(user_error=rejected)
    _install(monkeypatch, clients)
    owner = await _user()
    agent_id = await _agent(owner)
    await _store_key(owner, "openai", KEY_A)

    async with TestSessionLocal() as db:
        with pytest.raises(LLMUnavailableError):
            await run_agent_cycle(agent_id, db)

    cycle = (await _cycles(agent_id))[-1]
    assert cycle.status == CycleStatus.FAILED and cycle.failure_code == LLM_UNAVAILABLE
    assert cycle.failure_message == "OpenAI API error: AuthenticationError (HTTP 401)"
    assert cycle.snapshot_json["observability"]["llm_key_source"] == LLM_KEY_SOURCE_USER
    assert clients.server_builds == 0


@pytest.mark.asyncio
async def test_undecryptable_stored_key_fails_the_cycle_without_falling_back(
    db_session, monkeypatch
):
    clients = _Clients()
    _install(monkeypatch, clients)
    owner = await _user()
    agent_id = await _agent(owner)
    async with TestSessionLocal() as db:
        db.add(LLMCredential(user_id=owner, provider="openai", encrypted_api_key="garbage",
                             is_connected=True))
        await db.commit()

    async with TestSessionLocal() as db:
        with pytest.raises(LLMUnavailableError):
            await run_agent_cycle(agent_id, db)

    cycle = (await _cycles(agent_id))[-1]
    assert cycle.failure_code == LLM_UNAVAILABLE
    assert "reconnect it in Settings" in cycle.failure_message
    assert clients.server_builds == 0 and clients.user_clients == []


@pytest.mark.asyncio
async def test_scheduled_sweeps_build_each_cycles_client_from_its_own_owner(
    db_session, monkeypatch
):
    """Two owners' agents swept in turn (as one scheduler tick does, Celery or inline):
    every cycle gets a fresh client from its own owner's key; a keyless owner gets the
    server's client."""
    clients = _Clients()
    _install(monkeypatch, clients)
    owner_a, owner_b, owner_c = await _user(), await _user(), await _user()
    agent_a = await _agent(owner_a, watchlist=("AAPL", "MSFT"))
    agent_b = await _agent(owner_b, watchlist=("AAPL", "MSFT"))
    agent_c = await _agent(owner_c, watchlist=("AAPL",))
    await _store_key(owner_a, "openai", KEY_A)
    await _store_key(owner_b, "anthropic", KEY_B)

    for agent_id in (agent_a, agent_b, agent_c):
        async with TestSessionLocal() as db:
            cycles = await run_watchlist_cycles(agent_id, db, trigger=CycleTrigger.SCHEDULED)
            assert [c.status for c in cycles] == [CycleStatus.HOLD] * len(cycles)

    assert [(c.provider, c.api_key) for c in clients.user_clients] == [
        ("openai", KEY_A), ("openai", KEY_A), ("anthropic", KEY_B), ("anthropic", KEY_B)
    ]
    # A fresh client per cycle: never cached, never shared.
    assert len({id(c) for c in clients.user_clients}) == 4
    assert clients.server_builds == 1 and clients.server.generate_calls == 1
    sources = {
        agent_id: [c.snapshot_json["observability"]["llm_key_source"]
                   for c in await _cycles(agent_id)]
        for agent_id in (agent_a, agent_b, agent_c)
    }
    assert sources == {
        agent_a: [LLM_KEY_SOURCE_USER] * 2,
        agent_b: [LLM_KEY_SOURCE_USER] * 2,
        agent_c: [LLM_KEY_SOURCE_SERVER],
    }


@pytest.mark.asyncio
async def test_a_key_revoked_during_a_sweep_applies_from_the_next_symbol(
    db_session, monkeypatch
):
    """The sweep reuses one session; the credential must be re-read, not the cached row."""
    clients = _Clients()
    _install(monkeypatch, clients)
    owner = await _user()
    agent_id = await _agent(owner, watchlist=("AAPL", "MSFT"))
    await _store_key(owner, "openai", KEY_A)

    async def revoke_then_hold(prompt, *, system=None, temperature=None) -> str:
        async with TestSessionLocal() as other:  # the user clicks Disconnect mid-sweep
            await llm_keys.revoke_llm_credential(other, owner)
            await other.commit()
        return json.dumps(HOLD)

    real_build = clients.build_user

    def build_revoking(provider, api_key, settings):
        client = real_build(provider, api_key, settings)
        client.generate = revoke_then_hold
        return client

    monkeypatch.setattr(llm_keys, "create_user_llm_client", build_revoking)
    async with TestSessionLocal() as db:
        await run_watchlist_cycles(agent_id, db, trigger=CycleTrigger.SCHEDULED)

    sources = [c.snapshot_json["observability"]["llm_key_source"] for c in await _cycles(agent_id)]
    assert sources == [LLM_KEY_SOURCE_USER, LLM_KEY_SOURCE_SERVER]
    assert len(clients.user_clients) == 1 and clients.server_builds == 1


# --- Replay: the caller's key -------------------------------------------------------


def _replay_body() -> dict:
    now = datetime.now(UTC)
    bars = [
        {"timestamp": (now - timedelta(minutes=5 * (5 - i))).isoformat(), "open": 1,
         "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(5)
    ]
    return {"samples": [{"symbols": ["AAPL"], "bars_per_symbol": {"AAPL": bars}}]}


@pytest.mark.asyncio
async def test_replay_uses_the_callers_key_or_fails_without_one(client, monkeypatch):
    clients = _Clients()
    _install(monkeypatch, clients)
    monkeypatch.setattr("app.agent.router.get_llm_client", clients.build_server)
    auth_a, user_a = await _login(client)
    auth_b, _user_b = await _login(client)
    await _store_key(user_a, "openai", KEY_A)

    replay_a = await client.post("/agent/replay", headers=auth_a, json=_replay_body())
    monkeypatch.setattr(get_settings(), "llm_server_key_fallback", False)
    replay_b = await client.post("/agent/replay", headers=auth_b, json=_replay_body())

    assert replay_a.status_code == 200, replay_a.text
    assert [(c.provider, c.api_key, c.generate_calls) for c in clients.user_clients] == [
        ("openai", KEY_A, 1)
    ]
    assert replay_b.status_code == 503
    assert replay_b.json() == {"detail": f"LLM unavailable: {llm_keys.NO_KEY_AND_NO_FALLBACK}"}
    assert clients.server_builds == 0
    _assert_no_key(replay_a.text + replay_b.text, KEY_A)
