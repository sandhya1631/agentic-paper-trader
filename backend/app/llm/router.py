from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/health")
async def llm_health() -> dict:
    """Reports the configured LLM provider/model — no API call, no cost."""
    settings = get_settings()
    provider = settings.llm_provider.lower()
    model = settings.openai_model if provider == "openai" else settings.ollama_model
    return {"provider": provider, "model": model}
