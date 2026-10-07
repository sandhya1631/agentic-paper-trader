import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.llm.base import LLMHealth
from app.llm.factory import create_llm_client
from app.llm.ollama_client import OllamaClient
from app.llm.openai_client import OpenAIClient

# --- /llm/health endpoint (no API call, no cost) ---


@pytest.mark.asyncio
async def test_llm_health_reports_configured_provider(client: AsyncClient) -> None:
    response = await client.get("/llm/health")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] in ("openai", "ollama")
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


def test_factory_rejects_unsupported_provider():
    settings = Settings(llm_provider="anthropic")
    with pytest.raises(ValueError):
        create_llm_client(settings)


# --- OpenAIClient ---


def test_openai_client_requires_api_key():
    with pytest.raises(ValueError):
        OpenAIClient(api_key=None, model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_openai_client_generate_calls_chat_completions():
    mock_message = MagicMock(content="hello from openai")
    mock_completion = MagicMock(choices=[MagicMock(message=mock_message)])

    with patch("app.llm.openai_client.AsyncOpenAI") as mock_openai_cls:
        mock_instance = mock_openai_cls.return_value
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_completion)

        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        result = await client.generate("hi", system="be nice", temperature=0.5)

    assert result == "hello from openai"
    mock_instance.chat.completions.create.assert_called_once_with(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.5,
    )


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
    with patch("app.llm.openai_client.AsyncOpenAI") as mock_openai_cls:
        mock_instance = mock_openai_cls.return_value
        mock_instance.models.list = AsyncMock(return_value=listing)

        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        health = await client.health_check()

    assert health.reachable is True
    assert health.model_available is True
    assert health.provider == "openai"


@pytest.mark.asyncio
async def test_openai_health_check_unreachable_does_not_raise():
    with patch("app.llm.openai_client.AsyncOpenAI") as mock_openai_cls:
        mock_instance = mock_openai_cls.return_value
        mock_instance.models.list = AsyncMock(side_effect=RuntimeError("boom"))

        client = OpenAIClient(api_key="fake-key", model="gpt-4o-mini")
        health = await client.health_check()

    assert health.reachable is False
    assert health.model_available is False


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
