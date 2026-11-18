from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.constants import SUPPORTED_PROVIDERS
from app.llm.base import LLMClient, LLMUnavailableError
from app.llm.ollama_client import OllamaClient
from app.llm.openai_client import OpenAIClient


def create_llm_client(settings: Settings | None = None) -> LLMClient:
    """LLM Client Factory — returns the provider selected by LLM_PROVIDER.

    Orchestration code should depend only on the LLMClient interface, never on
    a specific provider, so switching LLM_PROVIDER in .env (openai <-> ollama <->
    anthropic) is the only change needed to move between a cloud model and a local one.
    The Anthropic client is imported lazily inside its own branch, so a missing SDK or
    key can never affect the openai/ollama paths.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
    if provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
            json_format=settings.ollama_json_format,
            timeout_seconds=settings.ollama_timeout_seconds,
            keep_alive_minutes=settings.ollama_keep_alive_minutes,
        )
    if provider == "anthropic":
        return _anthropic_client(settings.anthropic_api_key, settings.anthropic_model)

    raise LLMUnavailableError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        f"Expected one of: {SUPPORTED_PROVIDERS}"
    )


def _anthropic_client(api_key: str | None, model: str) -> LLMClient:
    try:
        from app.llm.anthropic_client import AnthropicClient
    except ImportError as exc:
        # The cause is kept for server-side tracebacks (no secret in an ImportError).
        raise LLMUnavailableError(
            "LLM_PROVIDER=anthropic requires the 'anthropic' package (pip install -e '.[dev]')"
        ) from exc
    return AnthropicClient(api_key=api_key, model=model)


def create_user_llm_client(provider: str, api_key: str, settings: Settings) -> LLMClient:
    """A client for a user's own key (#191), with the server's model for that provider.

    The model stays the server's because it must support schema-forced JSON. Built per
    cycle or replay and never cached, so one user's key never serves another user.
    """
    if provider == "openai":
        return OpenAIClient(api_key=api_key, model=settings.openai_model)
    if provider == "anthropic":
        return _anthropic_client(api_key, settings.anthropic_model)
    raise LLMUnavailableError(f"Unsupported AI provider '{provider}' for a user key")


def configured_model(settings: Settings, provider: str | None = None) -> str:
    """Model id for ``provider`` (default: LLM_PROVIDER). Reads no keys."""
    provider = (provider or settings.llm_provider).lower()
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    return settings.ollama_model


@lru_cache
def get_llm_client() -> LLMClient:
    """Cached singleton client, for use as a FastAPI dependency."""
    return create_llm_client()
