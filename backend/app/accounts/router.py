from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import (
    delete_alpaca_credentials,
    get_alpaca_account,
    save_alpaca_credentials,
)
from app.accounts.llm_keys import (
    effective_llm,
    get_llm_credential,
    revoke_llm_credential,
    save_llm_credential,
)
from app.accounts.schemas import (
    AlpacaCredentialsCreate,
    LLMKeyCreate,
    LLMKeyRead,
    LLMKeyStatusRead,
    TradingAccountRead,
)
from app.audit import record_audit_event
from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.core.constants import LLM_KEY_CONNECTED, LLM_KEY_DISCONNECTED
from app.db.models import User
from app.db.session import get_db
from app.llm.base import LLMUnavailableError
from app.llm.factory import create_user_llm_client

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/alpaca", response_model=TradingAccountRead, status_code=status.HTTP_201_CREATED)
async def connect_alpaca_account(
    payload: AlpacaCredentialsCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TradingAccountRead:
    """Encrypt and store the current user's Alpaca credentials. Secrets are never echoed back."""
    account = await save_alpaca_credentials(
        db,
        current_user.id,
        payload.api_key,
        payload.api_secret,
        is_paper=payload.is_paper,
    )
    return account


@router.get("/alpaca", response_model=TradingAccountRead)
async def get_alpaca_account_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TradingAccountRead:
    """Connection status only — never returns the encrypted or decrypted secrets."""
    account = await get_alpaca_account(db, current_user.id)
    if account is None or not account.is_connected:
        # A revoked account keeps its row (an agent may reference it) but reads as "not
        # connected" so the client shows the connect form again.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No Alpaca account connected"
        )
    return account


@router.delete("/alpaca", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_alpaca_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    deleted = await delete_alpaca_credentials(db, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No Alpaca account connected"
        )


# --- The user's own AI provider key (#191) ---
# Any signed-in user may connect a key: it only affects their own agent, and running or
# scheduling cycles still needs OPERATOR (#176). Every route acts only on the caller's row.
# The key is never echoed: 422s carry the provider and error type / HTTP status only, and
# main.py strips the submitted body from this route's validation errors.

_PROVIDER_LABELS = {"openai": "OpenAI", "anthropic": "Claude"}


@router.post("/llm", response_model=LLMKeyRead, status_code=status.HTTP_201_CREATED)
async def connect_llm_key(
    payload: LLMKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LLMKeyRead:
    """Verify the key with the provider's free model check, then store it encrypted."""
    settings = get_settings()
    label = _PROVIDER_LABELS[payload.provider]
    try:
        client = create_user_llm_client(payload.provider, payload.api_key, settings)
    except LLMUnavailableError as exc:
        # A missing SDK on the server; static text, no key.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail) from None
    health = await client.health_check()
    if not (health.reachable and health.model_available):
        # health.detail names only the error type and HTTP status (never the provider's
        # body, which for OpenAI quotes part of the key) or the model's availability.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} did not accept this key, so it was not saved: {health.detail}",
        )
    credential = await save_llm_credential(db, current_user.id, payload.provider, payload.api_key)
    record_audit_event(
        db,
        event_type=LLM_KEY_CONNECTED,
        entity_type="llm_credential",
        entity_id=credential.id,
        actor_id=current_user.id,
        details={"provider": payload.provider},
    )
    await db.commit()
    await db.refresh(credential)
    return LLMKeyRead.model_validate(credential)


@router.get("/llm", response_model=LLMKeyStatusRead)
async def get_llm_key_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LLMKeyStatusRead:
    """Always 200: the caller's key status and which key their cycles use now."""
    credential = await get_llm_credential(db, current_user.id)
    stored = credential if credential is not None and credential.is_connected else None
    effective = effective_llm(credential, get_settings())
    return LLMKeyStatusRead(
        connected=stored is not None,
        provider=stored.provider if stored else None,
        last_verified_at=stored.last_verified_at if stored else None,
        effective_source=effective.key_source if effective else None,
        effective_provider=effective.provider if effective else None,
        effective_model=effective.model if effective else None,
    )


@router.delete("/llm", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_llm_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke the caller's key: the ciphertext is cleared and the row kept."""
    credential = await revoke_llm_credential(db, current_user.id)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No AI provider key connected")
    record_audit_event(
        db,
        event_type=LLM_KEY_DISCONNECTED,
        entity_type="llm_credential",
        entity_id=credential.id,
        actor_id=current_user.id,
        details={"provider": credential.provider},
    )
    await db.commit()
