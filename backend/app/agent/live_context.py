"""Live paper-account data for the agent's context (Feature #64 / Story #66).

The LLM must see the REAL account (cash, equity, positions, open orders) and whether
the market is open, not invented defaults. This module fetches them from Alpaca with
the user's own credentials. It is best-effort and fail-safe: if anything cannot be
read it returns None, and the context then says "UNAVAILABLE" so the prompt's HOLD
rule applies instead of the model trusting fabricated numbers. Credentials are never
returned or logged.
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import get_decrypted_alpaca_credentials
from app.broker.alpaca import AlpacaClient, fetch_all_orders
from app.broker.schemas import AlpacaOrderRead
from app.core.config import get_settings
from app.db.models import AgentConfig, TradingAccount
from app.risk.market_hours import fetch_market_clock_for

logger = logging.getLogger(__name__)


class Unset:
    """Marker meaning "the caller did not provide live data, so fetch it"."""


UNSET = Unset()


@dataclass(frozen=True)
class LiveAccountData:
    """Sanitized, JSON-safe account state: no credentials, plain strings for numbers."""

    account: dict[str, str]
    positions: list[dict[str, Any]]
    open_orders: list[dict[str, Any]]
    market_open: bool | None


def _open_order_entry(order: AlpacaOrderRead) -> dict[str, Any]:
    """One open order for the context. A notional order (#177) has no qty, so it is
    listed with its dollar amount instead."""
    entry: dict[str, Any] = {
        "id": str(order.id),
        "symbol": order.symbol,
        "side": order.side,
        "qty": str(order.qty) if order.qty is not None else None,
        "status": order.status,
    }
    if order.notional is not None:
        entry["notional"] = str(order.notional)
    return entry


async def fetch_live_account_data(db: AsyncSession, agent: AgentConfig) -> LiveAccountData | None:
    """Read the agent owner's real paper account. Returns None when it cannot be read."""
    try:
        result = await db.execute(
            select(TradingAccount).where(TradingAccount.id == agent.account_id)
        )
        account_row = result.scalar_one_or_none()
        if account_row is None or not account_row.is_connected or not account_row.is_paper:
            return None

        api_key, api_secret = await get_decrypted_alpaca_credentials(db, account_row.user_id)
        async with AlpacaClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=get_settings().alpaca_paper_base_url,
        ) as client:
            account = await client.get_account()
            positions = await client.get_positions()
            orders = await fetch_all_orders(client, status="open")
            clock = await fetch_market_clock_for(client)

        return LiveAccountData(
            account={
                "cash": str(account.cash),
                "equity": str(account.equity),
                "buying_power": str(account.buying_power),
                "daily_pnl": str(account.daily_pnl),
            },
            positions=[
                {
                    "symbol": position.symbol,
                    "qty": str(position.qty),
                    "market_value": str(position.market_value),
                    "unrealized_pnl": str(position.unrealized_pl),
                }
                for position in positions
            ],
            open_orders=[_open_order_entry(order) for order in orders],
            market_open=bool(clock.is_open),
        )
    except Exception as exc:
        # Log only the error type: never the message, which could echo request details.
        logger.warning(
            "Live account data unavailable for the agent context: %s", type(exc).__name__
        )
        return None