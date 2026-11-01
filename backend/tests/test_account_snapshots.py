from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.account_snapshots import save_account_snapshot
from app.broker.schemas import AlpacaAccountRead
from app.db.models import AccountSnapshot, TradingAccount, User


@pytest.mark.asyncio
async def test_save_account_snapshot_persists_daily_pnl(db_session: AsyncSession):
    user = User(email=f"snapshot-{uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="encrypted-key",
        encrypted_api_secret="encrypted-secret",
        is_paper=True,
        is_connected=True,
    )
    db_session.add(account)
    await db_session.flush()

    summary = AlpacaAccountRead(
        id="alpaca-account-1",
        status="ACTIVE",
        cash=Decimal("9000.00"),
        portfolio_value=Decimal("10100.00"),
        buying_power=Decimal("18000.00"),
        equity=Decimal("10100.00"),
        last_equity=Decimal("10000.00"),
    )

    snapshot = await save_account_snapshot(db_session, account.id, summary)
    stored = (
        await db_session.execute(
            select(AccountSnapshot).where(AccountSnapshot.id == snapshot.id)
        )
    ).scalar_one()

    assert stored.account_id == account.id
    assert stored.equity == Decimal("10100.0000")
    assert stored.daily_pnl == Decimal("100.0000")
    assert stored.daily_pnl_percent == Decimal("1.000000")
