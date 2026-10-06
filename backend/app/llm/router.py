from fastapi import APIRouter, Query, Response, status

from app.core.config import get_settings
from app.llm.factory import get_llm_client

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/health")
async def llm_health(
    response: Response,
    probe: bool = Query(
        default=False,
        description=(
            "When true, actively contacts the configured LLM backend to verify "
            "reachability and model availability (returns 503 if unreachable). "
            "When false (default), only echoes the configured provider/model "
            "— no API call, no cost."
        ),
    ),
) -> dict:
    """Report the configured LLM provider/model, optionally probing connectivity."""
    settings = get_settings()
    provider = settings.llm_provider.lower()
    model = settings.openai_model if provider == "openai" else settings.ollama_model

    if not probe:
        return {"provider": provider, "model": model}

    health = await get_llm_client().health_check()
    if not health.reachable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "provider": health.provider,
        "model": health.model,
        "reachable": health.reachable,
        "model_available": health.model_available,
        "detail": health.detail,
    }
