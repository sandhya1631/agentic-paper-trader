from fastapi import APIRouter

from app.core.constants import (
    CURRENCY,
    DEFAULT_WATCHLIST,
    LLM_KEY_PROVIDERS,
    MAX_WATCHLIST_SIZE,
    OLLAMA_KEEP_ALIVE_MINUTES_RANGE,
    OLLAMA_TIMEOUT_SECONDS_RANGE,
)
from app.db.enums import (
    ApprovalMode,
    ApprovalStatus,
    CycleStatus,
    CycleTrigger,
    DecisionAction,
    PolicyDecisionType,
)
from app.meta.schemas import ConstantsRead

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get(
    "/constants",
    response_model=ConstantsRead,
    summary="Backend constants/enums the frontend should never hardcode",
)
async def get_constants() -> ConstantsRead:
    """Return currency and every enum value a client needs to render cycle/
    approval state, so the frontend reads these instead of hardcoding them."""
    return ConstantsRead(
        currency=CURRENCY,
        cycle_trigger=[v.value for v in CycleTrigger],
        cycle_status=[v.value for v in CycleStatus],
        decision_action=[v.value for v in DecisionAction],
        policy_decision_type=[v.value for v in PolicyDecisionType],
        approval_mode=[v.value for v in ApprovalMode],
        approval_status=[v.value for v in ApprovalStatus],
        max_watchlist_size=MAX_WATCHLIST_SIZE,
        default_watchlist=list(DEFAULT_WATCHLIST),
        llm_timeout_seconds_range=list(OLLAMA_TIMEOUT_SECONDS_RANGE),
        llm_keep_alive_minutes_range=list(OLLAMA_KEEP_ALIVE_MINUTES_RANGE),
        llm_key_providers=list(LLM_KEY_PROVIDERS),
    )
