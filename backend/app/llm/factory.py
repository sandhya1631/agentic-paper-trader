from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.constants import SUPPORTED_PROVIDERS
from app.llm.base import LLMClient
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
        return OllamaClient(base_url=settings.ollama_base_url, model=settings.ollama_model)
    if provider == "anthropic":
        try:
            from app.llm.anthropic_client import AnthropicClient
        except ImportError as exc:
            raise ValueError(
                "LLM_PROVIDER=anthropic requires the 'anthropic' package (pip install -e '.[dev]')"
            ) from exc
        return AnthropicClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        f"Expected one of: {SUPPORTED_PROVIDERS}"
    )


def configured_model(settings: Settings) -> str:
    """Model id for the configured provider (reads no keys; used by /llm/health)."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    return settings.ollama_model


@lru_cache
def get_llm_client() -> LLMClient:
    """Cached singleton client, for use as a FastAPI dependency."""
    return create_llm_client()
