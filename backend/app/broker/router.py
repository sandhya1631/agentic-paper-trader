import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import (
    CredentialVaultError,
    get_alpaca_account,
    get_decrypted_alpaca_credentials,
)
from app.audit import record_audit_event
from app.auth.dependencies import get_current_user, require_operator
from app.broker.account_snapshots import save_account_snapshot
from app.broker.alpaca import AlpacaClient
from app.broker.schemas import (
    AlpacaAccountRead,
    AlpacaOrderRead,
    AlpacaPositionRead,
    MarketSnapshotRead,
    OrderCreate,
)
from app.core.config import get_settings
from app.core.constants import (
    MANUAL_ORDER_BLOCKED,
    MANUAL_ORDER_FAILED,
    MANUAL_ORDER_SUBMITTED,
)
from app.db.models import User
from app.db.session import get_db
from app.risk.kill_switch import is_kill_switch_enabled

logger = logging.getLogger(__name__)

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
    if account is not None and account.is_connected:
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
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        account_summary = await client.get_account()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )
    account = await get_alpaca_account(db, current_user.id)
    if account is not None:
        try:
            await save_account_snapshot(db, account.id, account_summary)
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Account summary was fetched but could not be persisted.",
            ) from exc
    return account_summary


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
    current_user: Annotated[User, Depends(require_operator)],
    client: Annotated[AlpacaClient, Depends(get_alpaca_client)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Manual override order: kill switch only, no P-0xx policies. Every attempt that gets
    this far is audited (#162) as SUBMITTED, BLOCKED (423) or FAILED (502)."""
    if await is_kill_switch_enabled(db):
        await _audit_manual_order(
            db,
            current_user,
            MANUAL_ORDER_BLOCKED,
            order,
            outcome="BLOCKED",
            reason="Order submission blocked by the global kill switch",
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Order submission blocked by the global kill switch",
        )
    try:
        placed = await client.submit_order(order)
    except Exception as exc:
        await _audit_manual_order(
            db,
            current_user,
            MANUAL_ORDER_FAILED,
            order,
            outcome="FAILED",
            broker_error=_without_credentials(str(exc), client),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca API error: {exc}",
        )
    await _audit_manual_order(
        db,
        current_user,
        MANUAL_ORDER_SUBMITTED,
        order,
        outcome="SUBMITTED",
        broker_order_id=placed.id,
        broker_status=placed.status,
    )
    return placed


async def _audit_manual_order(
    db: AsyncSession,
    user: User,
    event_type: str,
    order: OrderCreate,
    **result: Any,
) -> None:
    """Write and COMMIT one audit_events row for a manual order attempt.

    Committed here, before any HTTPException, because the request session is never
    committed for an error response. The order itself lives at Alpaca, so it cannot share
    a transaction with this row: if the commit fails, the error is logged and the
    response stays what it would have been (a placed order is still reported as placed,
    so nobody retries it into a duplicate).
    """
    details: dict[str, Any] = {
        "symbol": order.symbol.upper(),
        "side": order.side.value,
        "quantity": order.qty,
        "order_type": order.type.value,
        "time_in_force": order.time_in_force.value,
        "limit_price": str(order.limit_price) if order.limit_price is not None else None,
        "client_order_id": order.client_order_id,
        **result,
    }
    record_audit_event(
        db,
        event_type=event_type,
        entity_type="manual_order",
        entity_id=None,
        actor_id=user.id,
        details=details,
    )
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "Could not persist the %s audit event (broker order id: %s)",
            event_type,
            result.get("broker_order_id"),
        )


def _without_credentials(message: str, client: AlpacaClient) -> str:
    """Strip the Alpaca key and secret from a broker error message, should one echo them."""
    headers = getattr(client, "headers", None) or {}
    for name in ("APCA-API-KEY-ID", "APCA-API-SECRET-KEY"):
        secret = headers.get(name)
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


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
