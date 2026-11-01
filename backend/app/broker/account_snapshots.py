from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.schemas import AlpacaAccountRead
from app.db.models import AccountSnapshot


async def save_account_snapshot(
    db: AsyncSession, account_id: UUID, account: AlpacaAccountRead
) -> AccountSnapshot:
    """Persist the non-sensitive account metrics returned by Alpaca."""
    daily_pnl = account.equity - account.last_equity
    daily_pnl_percent = (
        daily_pnl / account.last_equity * Decimal("100")
        if account.last_equity
        else None
    )
    snapshot = AccountSnapshot(
        account_id=account_id,
        currency=account.currency,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        buying_power=account.buying_power,
        equity=account.equity,
        last_equity=account.last_equity,
        daily_pnl=daily_pnl,
        daily_pnl_percent=daily_pnl_percent,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot
