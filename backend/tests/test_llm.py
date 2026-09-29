from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import Settings
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
