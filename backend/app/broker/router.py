from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import (
    CredentialVaultError,
    get_alpaca_account,
    get_decrypted_alpaca_credentials,
)
from app.auth.dependencies import get_current_user
from app.broker.alpaca import AlpacaClient
from app.broker.schemas import (
    AlpacaAccountRead,
    AlpacaOrderRead,
    AlpacaPositionRead,
    MarketSnapshotRead,
    OrderCreate,
)
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/broker", tags=["Broker Integration"])


async def get_alpaca_client(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_alpaca_api_key: str | None = Header(
        default=None,
        alias="X-Alpaca-API-Key",
        description="Dev-only Alpaca API Key override (ignored outside development).",
    ),
    x_alpaca_api_secret: str | None = Header(
        default=None,
        alias="X-Alpaca-API-Secret",
        description="Dev-only Alpaca API Secret override (ignored outside development).",
    ),
) -> AlpacaClient:
    """Build an AlpacaClient from the current user's encrypted, vault-stored credentials.

    Resolution order:
      1. The authenticated user's connected credentials, decrypted from the vault at call time.
      2. (development only) X-Alpaca-* headers or ALPACA_* from .env, as a convenience fallback.
    Outside development a user with no connected account is rejected — the broker is never
    called. Decrypted secrets are used only to construct the client and are never logged or
    returned to a client.
    """
    settings = get_settings()

    account = await get_alpaca_account(db, current_user.id)
    if account is not None:
        try:
            api_key, api_secret = await get_decrypted_alpaca_credentials(db, current_user.id)
        except CredentialVaultError as exc:
            # Account exists but the stored ciphertext can't be decrypted (e.g. the
            # encryption key changed). Fail loudly rather than silently using .env.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Stored Alpaca credentials could not be decrypted. "
                    "Reconnect your account via POST /accounts/alpaca."
                ),
            ) from exc
        return AlpacaClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=settings.alpaca_paper_base_url,
        )

    # No connected account for this user — dev-only plaintext fallback.
    if settings.app_env.lower() == "development":
        api_key = x_alpaca_api_key or settings.alpaca_api_key
        api_secret = x_alpaca_api_secret or settings.alpaca_api_secret
        if api_key and api_secret and "your_alpaca" not in api_key.lower():
            return AlpacaClient(
                api_key=api_key,
                api_secret=api_secret,
                base_url=settings.alpaca_paper_base_url,
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "No Alpaca account connected. Connect your paper-trading credentials "
            "via POST /accounts/alpaca."
        ),
    )


@router.get(
    "/account",
    response_model=AlpacaAccountRead,
    summary="Get Alpaca paper trading account summary",
    description=(
        "Fetch live paper trading account summary details including portfolio equity, "
        "cash balance, buying power, and account status."
    ),
)
async def get_account_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    try:
        return await client.get_account()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.get(
    "/positions",
    response_model=list[AlpacaPositionRead],
    summary="Get open paper trading positions",
    description="Fetch list of all currently open paper trading stock positions.",
)
async def get_open_positions(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    try:
        return await client.get_positions()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.get(
    "/snapshot/{symbol}",
    response_model=MarketSnapshotRead,
    summary="Get market price snapshot & indicators",
    description=(
        "Fetch latest stock price snapshot and computed technical indicators "
        "(14-period RSI, 20-period SMA, 50-period SMA) for a given symbol (e.g., AAPL)."
    ),
)
async def get_market_snapshot(
    symbol: str,
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    try:
        return await client.get_market_snapshot(symbol)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error fetching market snapshot for '{symbol}': {exc}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.post(
    "/orders",
    response_model=AlpacaOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Submit paper trading order",
    description="Submit a new paper trading BUY or SELL market/limit order to Alpaca.",
)
async def place_paper_order(
    order: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    try:
        return await client.submit_order(order)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.get(
    "/orders",
    response_model=list[AlpacaOrderRead],
    summary="Get paper trading order history",
    description="Fetch list of paper orders filtered by status ('open', 'closed', 'all').",
)
async def get_orders_list(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
    status_filter: str = Query(
        default="all",
        alias="status",
        description="Filter orders by status: 'open', 'closed', or 'all'.",
    ),
):
    try:
        return await client.get_orders(status=status_filter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )

