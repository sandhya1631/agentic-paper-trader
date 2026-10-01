from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMClient
from app.llm.ollama_client import OllamaClient
from app.llm.openai_client import OpenAIClient

SUPPORTED_PROVIDERS = ("openai", "ollama")


def create_llm_client(settings: Settings | None = None) -> LLMClient:
    """LLM Client Factory — returns the provider selected by LLM_PROVIDER.

    Orchestration code should depend only on the LLMClient interface, never on
    a specific provider, so switching LLM_PROVIDER in .env (openai <-> ollama)
    is the only change needed to move between a cloud model and a local one.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
    if provider == "ollama":
        return OllamaClient(base_url=settings.ollama_base_url, model=settings.ollama_model)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        f"Expected one of: {SUPPORTED_PROVIDERS}"
    )


@lru_cache
def get_llm_client() -> LLMClient:
    """Cached singleton client, for use as a FastAPI dependency."""
    return create_llm_client()
