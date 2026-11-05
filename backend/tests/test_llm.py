import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from httpx import AsyncClient
from test_llm_output_failure import INVALID_1, VALID_HOLD, _run_inputs

from app.agent.orchestrator import run_agent_cycle
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.core.config import Settings, get_settings
from app.core.constants import SUPPORTED_PROVIDERS
from app.db.enums import CycleStatus, DecisionAction
from app.db.models import AgentCycle
from app.llm.base import LLMHealth
from app.llm.decision_schema import DECISION_JSON_SCHEMA
from app.llm.factory import configured_model, create_llm_client
from app.llm.ollama_client import OllamaClient
from app.llm.openai_client import OpenAIClient
from app.schemas import DecisionProposal

OPENAI_SDK = "app.llm.openai_client.AsyncOpenAI"
# Fake, key-shaped value used to prove a key never leaks. OpenAI's 401 body quotes the key
# masked as its first 8 + last 4 characters ("Incorrect API key provided: sk-canar****Y9Q7"),
# so the tests look for those fragments too.
CANARY_KEY = "sk-canary-test-K3Y9Q7"
CANARY_MASKED = f"{CANARY_KEY[:8]}{'*' * 8}{CANARY_KEY[-4:]}"
CANARY_FRAGMENTS = (CANARY_KEY, CANARY_KEY[:8], CANARY_KEY[-4:])
OPENAI_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "decision_proposal", "schema": DECISION_JSON_SCHEMA, "strict": True},
}

# --- /llm/health endpoint (no API call, no cost) ---


@pytest.mark.asyncio
async def test_llm_health_reports_configured_provider(client: AsyncClient) -> None:
    response = await client.get("/llm/health")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] in SUPPORTED_PROVIDERS
    assert "model" in body


# --- /llm/health?probe=true actively probes the backend ---


@pytest.mark.asyncio
async def test_llm_health_probe_ok_returns_200(client: AsyncClient) -> None:
    healthy = LLMHealth(
        provider="ollama",
        model="llama3.1",
        reachable=True,
        model_available=True,
        detail="ok",
    )
    fake_client = MagicMock()
    fake_client.health_check = AsyncMock(return_value=healthy)

    with patch("app.llm.router.get_llm_client", return_value=fake_client):
        response = await client.get("/llm/health?probe=true")

    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is True
    assert body["model_available"] is True
    assert body["provider"] == "ollama"


@pytest.mark.asyncio
async def test_llm_health_probe_unreachable_returns_503(client: AsyncClient) -> None:
    unhealthy = LLMHealth(
        provider="ollama",
        model="llama3.1",
        reachable=False,
        model_available=False,
        detail="Ollama unreachable at http://ollama:11434",
    )
    fake_client = MagicMock()
    fake_client.health_check = AsyncMock(return_value=unhealthy)

    with patch("app.llm.router.get_llm_client", return_value=fake_client):
        response = await client.get("/llm/health?probe=true")

    assert response.status_code == 503
    assert response.json()["reachable"] is False


# --- Factory: LLM_PROVIDER switches the implementation, no orchestration changes ---


def test_factory_returns_ollama_client_by_default():
    settings = Settings(llm_provider="ollama", ollama_model="llama3.1")
    client = create_llm_client(settings)
    assert isinstance(client, OllamaClient)


def test_factory_returns_openai_client_when_configured():
    settings = Settings(
        llm_provider="openai", openai_api_key="fake-key", openai_model="gpt-4o-mini"
    )
    client = create_llm_client(settings)
    assert isinstance(client, OpenAIClient)


def test_unsupported_provider_still_errors():
    settings = Settings(_env_file=None, llm_provider="bogus", anthropic_api_key=None)
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER 'bogus'") as excinfo:
        create_llm_client(settings)
    assert "anthropic" in str(excinfo.value)  # the message lists every supported provider


# --- No regression when the Anthropic SDK/key are absent (#154) ---
# These tests deliberately do NOT import the anthropic SDK (see test_llm_anthropic.py).


def _block_anthropic_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any `import anthropic` raise ImportError and forget the client module."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.delitem(sys.modules, "app.llm.anthropic_client", raising=False)


@pytest.mark.parametrize(
    ("provider", "expected_cls", "expected_model"),
    [("ollama", OllamaClient, "llama3.1"), ("openai", OpenAIClient, "gpt-4o-mini")],
)
def test_no_anthropic_credentials_does_not_affect_default_provider(
    monkeypatch, provider, expected_cls, expected_model
):
    _block_anthropic_sdk(monkeypatch)
    # Stand-in with no anthropic_* attributes: reading one would raise AttributeError.
    settings = SimpleNamespace(
        llm_provider=provider,
        openai_api_key="fake-key",
        openai_model="gpt-4o-mini",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1",
    )

    client = create_llm_client(settings)

    assert isinstance(client, expected_cls)
    assert configured_model(settings) == expected_model
    assert "app.llm.anthropic_client" not in sys.modules


def test_app_import_unaffected_without_anthropic_sdk():
    code = (
        "import sys; sys.modules['anthropic'] = None; import app.main; "
        "assert 'app.llm.anthropic_client' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_anthropic_selected_without_sdk_errors_clearly(monkeypatch):
    _block_anthropic_sdk(monkeypatch)
    settings = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="fake-key")

    with pytest.raises(ValueError, match="requires the 'anthropic' package"):
        create_llm_client(settings)


# --- Shared decision schema (structured output for OpenAI and Claude) ---


def test_decision_schema_matches_decision_proposal():
    fields = set(DecisionProposal.model_fields)

    assert set(DECISION_JSON_SCHEMA["properties"]) == fields
    assert set(DECISION_JSON_SCHEMA["required"]) == fields
    assert set(DECISION_JSON_SCHEMA["properties"]["action"]["enum"]) == {
        action.value for action in DecisionAction
    }
    assert DECISION_JSON_SCHEMA["additionalProperties"] is False


def test_decision_schema_module_is_provider_neutral():
    """The shared schema imports no provider SDK and not app.schemas, so the OpenAI and
    Ollama paths can never depend on the anthropic package."""
    code = (
        "import sys; sys.modules['anthropic'] = None; "
        "import app.llm.decision_schema, app.llm.openai_client; "
        "assert 'app.schemas' not in sys.modules; "
        "assert 'app.llm.anthropic_client' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


# --- OpenAIClient ---


def _mock_openai(sdk_cls: MagicMock) -> MagicMock:
    """The patched AsyncOpenAI instance, usable as `async with AsyncOpenAI(...) as c`."""
    sdk = sdk_cls.return_value
    sdk.__aenter__.return_value = sdk
    sdk.__aexit__.return_value = False
    return sdk


def _completion(content=None, *, refusal=None, finish_reason="stop") -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _status_error(cls, status: int, message: str):
    """An SDK status error shaped like the real one (body = OpenAI's `error` object)."""
    response = MagicMock(status_code=status, headers={"x-request-id": "req_OPENAI_SECRET_ID"})
    body = {
        "message": message,
        "type": "invalid_request_error",
        "code": "invalid_api_key",
        "param": None,
    }
    return cls(message, response=response, body=body)


def test_openai_client_requires_api_key():
    with pytest.raises(ValueError):
        OpenAIClient(api_key=None, model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_openai_client_generate_calls_chat_completions():
    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.chat.completions.create = AsyncMock(return_value=_completion("hello from openai"))

        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        result = await client.generate("hi", system="be nice", temperature=0.5)

    assert result == "hello from openai"
    # A fresh SDK client per call, with an explicit timeout and retry budget.
    sdk_cls.assert_called_once_with(api_key="fake-key", timeout=120.0, max_retries=2)
    sdk.chat.completions.create.assert_called_once_with(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.5,
        response_format=OPENAI_RESPONSE_FORMAT,
    )
    # Strict structured output uses the shared schema (the same object Claude is sent).
    sent = sdk.chat.completions.create.await_args.kwargs["response_format"]
    assert sent["json_schema"]["schema"] is DECISION_JSON_SCHEMA


@pytest.mark.asyncio
async def test_openai_generate_returns_refusals_and_bad_content_as_text(caplog):
    """Content is never raised on: refusals, empty or malformed answers come back as text
    so validation (and the orchestrator's single self-repair retry) decides. A refusal's
    text is returned so the cycle's llm_raw_response records that the model refused."""
    refusal = "I can't help with that."
    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.chat.completions.create = AsyncMock(
            side_effect=[
                _completion(refusal=refusal),
                _completion(None),
                _completion("not json"),
                _completion('{"symbol": "AAPL"', finish_reason="length"),
                _completion("", finish_reason="content_filter"),
                SimpleNamespace(choices=[]),
            ]
        )
        client = OpenAIClient(api_key=CANARY_KEY, model="gpt-4o-mini")

        with caplog.at_level(logging.WARNING, logger="app.llm.openai_client"):
            answers = [await client.generate("p") for _ in range(6)]

    assert answers == [refusal, "", "not json", '{"symbol": "AAPL"', "", ""]
    for raw in answers:  # every one of these must end in validation, never a guessed trade
        with pytest.raises(ToolValidationError):
            validate_decision_proposal(raw)
    assert "OpenAI refused the request" in caplog.text
    assert refusal not in caplog.text  # the refusal text is recorded on the cycle, not logged
    assert "finish_reason=length" in caplog.text
    assert "finish_reason=content_filter" in caplog.text
    assert "had no choices" in caplog.text
    assert CANARY_KEY not in caplog.text


@pytest.mark.asyncio
async def test_openai_malformed_output_triggers_cycle_self_repair():
    agent, db, _, collector = _run_inputs()

    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.chat.completions.create = AsyncMock(
            side_effect=[_completion(INVALID_1), _completion(VALID_HOLD)]
        )
        llm_client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")

        cycle = await run_agent_cycle(agent.id, db, llm_client=llm_client, collector=collector)

    assert sdk.chat.completions.create.await_count == 2
    assert cycle.status == CycleStatus.HOLD
    assert cycle.failure_code is None
    # The repair request is schema-constrained too.
    for call in sdk.chat.completions.create.await_args_list:
        assert call.kwargs["response_format"] == OPENAI_RESPONSE_FORMAT


@pytest.mark.asyncio
async def test_openai_api_errors_are_sanitized(caplog):
    """An API failure surfaces only its type and HTTP status: the provider's response
    body (which can quote part of the key), request id and the key never reach the
    raised message (the cycle's failure_message, the API response and the UI)."""
    body_text = f"Incorrect API key provided: {CANARY_MASKED}. You can find your API key at ..."
    auth_error = _status_error(openai.AuthenticationError, 401, f"Error code: 401 - {body_text}")
    connection_error = openai.APIConnectionError(request=MagicMock())
    timeout_error = openai.APITimeoutError(request=MagicMock())

    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.chat.completions.create = AsyncMock(
            side_effect=[auth_error, connection_error, timeout_error]
        )
        client = OpenAIClient(api_key=CANARY_KEY, model="gpt-4o-mini")

        raised = []
        with caplog.at_level(logging.WARNING, logger="app.llm.openai_client"):
            for _ in range(3):
                with pytest.raises(RuntimeError) as excinfo:
                    await client.generate("p")
                raised.append(excinfo.value)

    assert [str(exc) for exc in raised] == [
        "OpenAI API error: AuthenticationError (HTTP 401)",
        "OpenAI API error: APIConnectionError",
        "OpenAI API error: APITimeoutError",
    ]
    for exc in raised:
        assert exc.__cause__ is None and exc.__suppress_context__
        for leaked in (*CANARY_FRAGMENTS, "Incorrect API key", "req_OPENAI_SECRET_ID"):
            assert leaked not in str(exc)
    # Server-side diagnostics keep the request id and the enum-like error fields (enough to
    # tell a bad key from an unsupported model or quota), never the message, body or key.
    assert "req_OPENAI_SECRET_ID" in caplog.text
    assert "code=invalid_api_key type=invalid_request_error" in caplog.text
    assert "Incorrect API key" not in caplog.text
    assert not any(fragment in caplog.text for fragment in CANARY_FRAGMENTS)


@pytest.mark.asyncio
async def test_openai_api_error_never_reaches_the_cycle_record():
    agent, db, _, collector = _run_inputs()
    auth_error = _status_error(
        openai.AuthenticationError,
        401,
        f"Error code: 401 - Incorrect API key provided: {CANARY_MASKED}",
    )

    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.chat.completions.create = AsyncMock(side_effect=auth_error)
        llm_client = OpenAIClient(api_key=CANARY_KEY, model="gpt-4o-mini")

        with pytest.raises(RuntimeError):
            await run_agent_cycle(agent.id, db, llm_client=llm_client, collector=collector)

    added = [call.args[0] for call in db.add.call_args_list]
    cycle = next(row for row in added if isinstance(row, AgentCycle))
    assert cycle.status == CycleStatus.FAILED
    assert cycle.failure_code == "UNEXPECTED_ERROR"
    assert cycle.failure_message == "OpenAI API error: AuthenticationError (HTTP 401)"
    stored = " ".join(
        str(value) for value in (cycle.failure_message, cycle.llm_raw_response, cycle.snapshot_json)
    )
    assert not any(fragment in stored for fragment in CANARY_FRAGMENTS)


def test_openai_uses_a_fresh_sdk_client_per_call_across_event_loops():
    """Celery runs each task under its own asyncio.run; the lru_cached client must not
    reuse an SDK client (and its connection pool) from an event loop that has closed."""
    instances: list[MagicMock] = []

    def new_sdk(**kwargs):
        sdk = MagicMock()
        sdk.__aenter__.return_value = sdk
        sdk.__aexit__.return_value = False
        sdk.chat.completions.create = AsyncMock(return_value=_completion(VALID_HOLD))
        instances.append(sdk)
        return sdk

    with patch(OPENAI_SDK, side_effect=new_sdk):
        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        first = asyncio.run(client.generate("p"))
        second = asyncio.run(client.generate("p"))

    assert first == second == VALID_HOLD
    assert len(instances) == 2
    for sdk in instances:
        sdk.chat.completions.create.assert_awaited_once()
        sdk.__aexit__.assert_awaited_once()  # each client is closed inside its own loop


@pytest.mark.asyncio
async def test_openai_selected_without_key_errors_clearly(client: AsyncClient):
    no_key = Settings(
        _env_file=None, llm_provider="openai", openai_api_key=None, openai_model="gpt-4o-mini"
    )

    with pytest.raises(ValueError, match="OPENAI_API_KEY is required when LLM_PROVIDER=openai"):
        create_llm_client(no_key)

    with (
        patch("app.llm.router.get_settings", return_value=no_key),
        patch("app.llm.router.get_llm_client", side_effect=lambda: create_llm_client(no_key)),
        patch(OPENAI_SDK) as sdk_cls,
    ):
        probe = await client.get("/llm/health?probe=true")

    # Caught and reported unavailable (503), not an unhandled 500.
    assert probe.status_code == 503
    assert probe.json() == {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "reachable": False,
        "model_available": False,
        "detail": "LLM client unavailable: OPENAI_API_KEY is required when LLM_PROVIDER=openai",
    }
    sdk_cls.assert_not_called()  # no SDK client is ever built without a key


# --- OllamaClient ---


@pytest.mark.asyncio
async def test_ollama_client_generate_calls_chat_api():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"message": {"content": "hello from ollama"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.llm.ollama_client.httpx.AsyncClient", return_value=mock_client):
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        result = await client.generate("hi", system="be nice")

    assert result == "hello from ollama"
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://localhost:11434/api/chat"
    assert kwargs["json"]["model"] == "llama3.1"
    assert kwargs["json"]["messages"] == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hi"},
    ]


# --- health_check (mocked transport) ---


def _mock_ollama_tags(models: list[str]):
    """Build a mocked httpx.AsyncClient whose GET returns the given tag list."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"models": [{"name": n} for n in models]}
    http = AsyncMock()
    http.get = AsyncMock(return_value=response)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    return http


@pytest.mark.asyncio
async def test_ollama_health_check_reachable_and_model_available():
    http = _mock_ollama_tags(["llama3.1:latest", "qwen2.5:0.5b"])
    with patch("app.llm.ollama_client.httpx.AsyncClient", return_value=http):
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        health = await client.health_check()

    assert health.reachable is True
    assert health.model_available is True
    assert health.provider == "ollama"
    http.get.assert_awaited_once_with("http://localhost:11434/api/tags")


@pytest.mark.asyncio
async def test_ollama_health_check_model_not_pulled():
    http = _mock_ollama_tags(["some-other-model:latest"])
    with patch("app.llm.ollama_client.httpx.AsyncClient", return_value=http):
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        health = await client.health_check()

    assert health.reachable is True
    assert health.model_available is False
    assert "ollama pull llama3.1" in health.detail


@pytest.mark.asyncio
async def test_ollama_health_check_unreachable_does_not_raise():
    http = AsyncMock()
    http.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)

    with patch("app.llm.ollama_client.httpx.AsyncClient", return_value=http):
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        health = await client.health_check()

    assert health.reachable is False
    assert health.model_available is False
    assert "unreachable" in health.detail.lower()


@pytest.mark.asyncio
async def test_openai_health_check_reachable():
    listing = MagicMock(data=[MagicMock(id="gpt-4o-mini"), MagicMock(id="gpt-4o")])
    other_models = MagicMock(data=[MagicMock(id="gpt-4o")])
    empty = MagicMock(data=[])
    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.models.list = AsyncMock(side_effect=[listing, other_models, empty])

        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        health = await client.health_check()
        missing = await client.health_check()
        unlisted = await client.health_check()

    assert health.reachable is True
    assert health.model_available is True
    assert health.provider == "openai"
    assert (missing.reachable, missing.model_available) == (True, False)
    assert missing.detail == "Model 'gpt-4o-mini' not available to this account"
    # An empty listing never claims the model is missing.
    assert (unlisted.reachable, unlisted.model_available, unlisted.detail) == (True, True, "ok")
    # A fresh SDK client per probe, with a short timeout, each one closed afterwards.
    assert sdk_cls.call_count == 3
    sdk_cls.assert_called_with(api_key="fake-key", timeout=10.0, max_retries=2)
    assert sdk.__aexit__.await_count == 3


@pytest.mark.asyncio
async def test_openai_health_check_unreachable_does_not_raise():
    with patch(OPENAI_SDK) as sdk_cls:
        sdk = _mock_openai(sdk_cls)
        sdk.models.list = AsyncMock(
            side_effect=_status_error(
                openai.AuthenticationError,
                401,
                f"Error code: 401 - Incorrect API key provided: {CANARY_MASKED}",
            )
        )

        client = OpenAIClient(api_key=CANARY_KEY, model="gpt-4o-mini")
        health = await client.health_check()

    assert health.reachable is False
    assert health.model_available is False
    # Type name only: this detail is shown in the UI's header LLM pill.
    assert health.detail == "OpenAI API unreachable: AuthenticationError"
    assert not any(fragment in health.detail for fragment in CANARY_FRAGMENTS)


# --- Live integration test (requires a real, running Ollama with the model pulled) ---
# Gated two ways: the `integration` marker is excluded in CI, and it is skipped
# unless OLLAMA_INTEGRATION is set, so a default local `pytest` run won't need Ollama.


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("OLLAMA_INTEGRATION"),
    reason="set OLLAMA_INTEGRATION=1 (with a running Ollama + pulled model) to run",
)
@pytest.mark.asyncio
async def test_ollama_live_health_and_generate():
    settings = get_settings()
    client = OllamaClient(base_url=settings.ollama_base_url, model=settings.ollama_model)

    health = await client.health_check()
    assert health.reachable is True, health.detail
    assert health.model_available is True, health.detail

    reply = await client.generate("Reply with exactly: VERIFY_OK")
    assert reply.strip() != ""
