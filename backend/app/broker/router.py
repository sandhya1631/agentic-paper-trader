from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.auth.dependencies import get_current_user
from app.broker.alpaca import AlpacaClient
from app.broker.schemas import (
    AlpacaAccountRead, AlpacaOrderRead, AlpacaPositionRead,
    MarketSnapshotRead, OrderCreate,
)
from app.core.config import get_settings
from app.db.models import User

router = APIRouter(prefix="/broker", tags=["Broker Integration"])


def get_alpaca_client(
    x_alpaca_api_key: str | None = Header(default=None, alias="X-Alpaca-API-Key"),
    x_alpaca_api_secret: str | None = Header(default=None, alias="X-Alpaca-API-Secret"),
) -> AlpacaClient:
    """Dependency that initializes an AlpacaClient using request headers or system settings."""
    settings = get_settings()

    api_key = x_alpaca_api_key or settings.alpaca_api_key
    api_secret = x_alpaca_api_secret or settings.alpaca_api_secret

    if not api_key or not api_secret or "your_alpaca" in api_key.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alpaca API credentials missing. Provide X-Alpaca-API-Key and X-Alpaca-API-Secret headers or set ALPACA_API_KEY / ALPACA_API_SECRET in backend/.env.",
        )

    return AlpacaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=settings.alpaca_paper_base_url,
    )


@router.get("/account", response_model=AlpacaAccountRead)
async def get_account_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    """Fetch live paper trading account summary (equity, cash, buying power, status)."""
    try:
        return await client.get_account()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.get("/positions", response_model=list[AlpacaPositionRead])
async def get_open_positions(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    """Fetch list of open paper positions."""
    try:
        return await client.get_positions()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )


@router.get("/snapshot/{symbol}", response_model=MarketSnapshotRead)
async def get_market_snapshot(
    symbol: str,
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    """Fetch latest stock price snapshot and computed technical indicators (14-period RSI, 20-period SMA)."""
    try:
        return await client.get_market_snapshot(symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error fetching market snapshot for '{symbol}': {exc}",
        )


@router.post("/orders", response_model=AlpacaOrderRead, status_code=status.HTTP_201_CREATED)
async def place_paper_order(
    order: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
):
    """Submit a new paper trading BUY/SELL order to Alpaca."""
    try:
        return await client.submit_order(order)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to place paper order: {exc}",
        )


@router.get("/orders", response_model=list[AlpacaOrderRead])
async def get_orders_list(
    current_user: Annotated[User, Depends(get_current_user)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
    status_filter: str = Query(default="all", alias="status"),
):
    """Fetch list of submitted paper orders."""
    try:
        return await client.get_orders(status=status_filter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )
