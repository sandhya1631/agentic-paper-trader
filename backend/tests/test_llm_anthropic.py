"""Anthropic (Claude) provider client (#154).

This module imports the anthropic SDK; the no-SDK regression tests live in test_llm.py.
The SDK class is always patched, so no test here ever makes a network call. Settings are
built explicitly (no .env, explicit key) because CI injects ANTHROPIC_API_KEY into the env.
"""

import logging
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from httpx import AsyncClient
from test_llm_output_failure import INVALID_1, VALID_HOLD, _run_inputs

from app.agent.orchestrator import run_agent_cycle
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.core.config import Settings
from app.db.enums import CycleStatus, DecisionAction
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMHealth
from app.llm.decision_schema import DECISION_JSON_SCHEMA
from app.llm.factory import configured_model, create_llm_client

FAKE_KEY = "sk-ant-test-not-a-real-key"
MODEL = "claude-opus-5-5"
SDK = "app.llm.anthropic_client.AsyncAnthropic"


def _settings(**overrides) -> Settings:
    values = {"llm_provider": "anthropic", "anthropic_api_key": None, "anthropic_model": MODEL}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _mock_sdk(sdk_cls: MagicMock) -> MagicMock:
    """The patched AsyncAnthropic instance, usable as `async with AsyncAnthropic(...) as c`."""
    sdk = sdk_cls.return_value
    sdk.__aenter__.return_value = sdk
    sdk.__aexit__.return_value = False
    return sdk


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _thinking() -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking="private reasoning", signature="sig")


def _response(*blocks, stop_reason="end_turn", stop_details=None) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=stop_details)


def test_factory_returns_anthropic_client():
    settings = _settings(anthropic_api_key=FAKE_KEY)

    client = create_llm_client(settings)

    assert isinstance(client, AnthropicClient)
    assert client.provider == "anthropic"
    assert configured_model(settings) == MODEL
    assert Settings.model_fields["anthropic_model"].default == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_anthropic_generate_parses_json(caplog):
    decision = '{"symbol": "AAPL", "action": "BUY", "quantity": 3, "reasoning": "RSI oversold"}'
    refusal_details = SimpleNamespace(category="general_harms")

    with patch(SDK) as sdk_cls:
        sdk = _mock_sdk(sdk_cls)
        sdk.beta.messages.create = AsyncMock(
            side_effect=[
                _response(_thinking(), _text(decision)),
                _response(_text("not json")),
                _response(stop_reason="refusal", stop_details=refusal_details),
            ]
        )
        client = AnthropicClient(api_key=FAKE_KEY, model=MODEL)

        raw = await client.generate("market context", system="S-001", temperature=0.7)
        malformed = await client.generate("market context")
        with caplog.at_level(logging.WARNING, logger="app.llm.anthropic_client"):
            refused = await client.generate("market context")

    # Thinking blocks are dropped; only the answer text comes back and validates.
    assert raw == decision
    proposal = validate_decision_proposal(raw)
    assert (proposal.symbol, proposal.action, proposal.quantity) == ("AAPL", DecisionAction.BUY, 3)

    # Request shape: structured output + effort, refusal fallbacks, and no sampling params.
    sdk_cls.assert_called_with(api_key=FAKE_KEY, timeout=120.0, max_retries=2)
    first_call = sdk.beta.messages.create.await_args_list[0]
    assert first_call.kwargs == {
        "model": MODEL,
        "max_tokens": 16000,
        "system": "S-001",
        "messages": [{"role": "user", "content": "market context"}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": DECISION_JSON_SCHEMA},
        },
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    assert "temperature" not in first_call.kwargs
    assert "thinking" not in first_call.kwargs
    assert "system" not in sdk.beta.messages.create.await_args_list[1].kwargs

    # Bad content is returned, never raised, so the orchestrator's self-repair can run.
    assert malformed == "not json"
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(malformed)

    # A refusal with no text returns "" and logs the stop reason, never the key.
    assert refused == ""
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(refused)
    assert "stop_reason=refusal" in caplog.text
    assert "general_harms" in caplog.text
    assert FAKE_KEY not in caplog.text


@pytest.mark.asyncio
async def test_anthropic_generate_omits_fallbacks_for_other_models():
    with patch(SDK) as sdk_cls:
        sdk = _mock_sdk(sdk_cls)
        sdk.beta.messages.create = AsyncMock(return_value=_response(_text(VALID_HOLD)))
        client = AnthropicClient(api_key=FAKE_KEY, model="claude-some-other-model")

        assert await client.generate("p") == VALID_HOLD

    kwargs = sdk.beta.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-some-other-model"
    assert "betas" not in kwargs
    assert "fallbacks" not in kwargs


@pytest.mark.asyncio
async def test_anthropic_api_errors_are_sanitized(caplog):
    """An API failure surfaces only its type and HTTP status: the provider's response
    body, request id and the key never reach the raised message (which becomes the
    cycle's failure_message, the API response and the UI); diagnostics go to the log."""
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    }
    auth_error = anthropic.AuthenticationError(
        f"Error code: 401 - {body} {FAKE_KEY}",
        response=MagicMock(status_code=401, headers={"request-id": "req_E2E_SECRET_ID"}),
        body=body,
    )
    connection_error = anthropic.APIConnectionError(request=MagicMock())

    with patch(SDK) as sdk_cls:
        sdk = _mock_sdk(sdk_cls)
        sdk.beta.messages.create = AsyncMock(side_effect=[auth_error, connection_error])
        client = AnthropicClient(api_key=FAKE_KEY, model=MODEL)

        with caplog.at_level(logging.WARNING, logger="app.llm.anthropic_client"):
            with pytest.raises(RuntimeError) as auth_raised:
                await client.generate("p")
            with pytest.raises(RuntimeError) as connection_raised:
                await client.generate("p")

    assert str(auth_raised.value) == "Anthropic API error: AuthenticationError (HTTP 401)"
    assert str(connection_raised.value) == "Anthropic API error: APIConnectionError"
    for raised in (auth_raised.value, connection_raised.value):
        assert raised.__cause__ is None and raised.__suppress_context__
        for leaked in ("invalid x-api-key", "authentication_error", "req_E2E_SECRET_ID", FAKE_KEY):
            assert leaked not in str(raised)
    # Server-side diagnostics keep the request id, never the key.
    assert "req_E2E_SECRET_ID" in caplog.text
    assert FAKE_KEY not in caplog.text


@pytest.mark.asyncio
async def test_anthropic_malformed_output_triggers_cycle_self_repair():
    agent, db, _, collector = _run_inputs()

    with patch(SDK) as sdk_cls:
        sdk = _mock_sdk(sdk_cls)
        sdk.beta.messages.create = AsyncMock(
            side_effect=[_response(_text(INVALID_1)), _response(_text(VALID_HOLD))]
        )
        llm_client = AnthropicClient(api_key=FAKE_KEY, model=MODEL)

        cycle = await run_agent_cycle(agent.id, db, llm_client=llm_client, collector=collector)

    assert sdk.beta.messages.create.await_count == 2
    assert cycle.status == CycleStatus.HOLD
    assert cycle.failure_code is None


@pytest.mark.asyncio
async def test_anthropic_health_reports_shape():
    not_found = anthropic.NotFoundError(
        "model not found", response=MagicMock(status_code=404), body=None
    )

    with patch(SDK) as sdk_cls:
        sdk = _mock_sdk(sdk_cls)
        sdk.models.retrieve = AsyncMock(
            side_effect=[MagicMock(id=MODEL), not_found, RuntimeError(f"boom {FAKE_KEY}")]
        )
        client = AnthropicClient(api_key=FAKE_KEY, model=MODEL)

        ok = await client.health_check()
        missing = await client.health_check()
        down = await client.health_check()

    for health in (ok, missing, down):
        assert isinstance(health, LLMHealth)
        assert set(asdict(health)) == {
            "provider",
            "model",
            "reachable",
            "model_available",
            "detail",
        }
        assert (health.provider, health.model) == ("anthropic", MODEL)
        assert FAKE_KEY not in health.detail

    assert (ok.reachable, ok.model_available, ok.detail) == (True, True, "ok")
    assert (missing.reachable, missing.model_available) == (True, False)
    assert (down.reachable, down.model_available) == (False, False)
    assert down.detail == "Anthropic API unreachable: RuntimeError"
    sdk.models.retrieve.assert_awaited_with(MODEL)
    sdk_cls.assert_called_with(api_key=FAKE_KEY, timeout=10.0, max_retries=2)


@pytest.mark.asyncio
async def test_anthropic_selected_without_key_errors_clearly(client: AsyncClient):
    no_key = _settings(anthropic_api_key=None)

    for settings in (no_key, _settings(anthropic_api_key="")):
        with pytest.raises(
            ValueError, match="ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
        ):
            create_llm_client(settings)

    with (
        patch("app.llm.router.get_settings", return_value=no_key),
        patch("app.llm.router.get_llm_client", side_effect=lambda: create_llm_client(no_key)),
        patch(SDK) as sdk_cls,
    ):
        echo = await client.get("/llm/health")
        probe = await client.get("/llm/health?probe=true")

    assert echo.status_code == 200
    assert echo.json() == {"provider": "anthropic", "model": MODEL}

    # Caught and reported unavailable (503), not an unhandled 500.
    assert probe.status_code == 503
    assert probe.json() == {
        "provider": "anthropic",
        "model": MODEL,
        "reachable": False,
        "model_available": False,
        "detail": (
            "LLM client unavailable: "
            "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
        ),
    }
    sdk_cls.assert_not_called()  # no SDK client is ever built without a key
