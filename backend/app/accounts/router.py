from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import (
    delete_alpaca_credentials,
    get_alpaca_account,
    save_alpaca_credentials,
)
from app.accounts.schemas import AlpacaCredentialsCreate, TradingAccountRead
from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db

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
    if account is None:
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
