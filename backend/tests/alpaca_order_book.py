"""A stand-in for Alpaca's GET /v2/orders, for the order-list tests of #177.

Honors what the real endpoint does with its query: the ``status`` filter, ``limit``
(capped at Alpaca's maximum), exclusive ``after`` / ``until`` bounds on submitted_at,
and ``direction`` (newest first by default). Every call's arguments are recorded.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.broker.schemas import AlpacaOrderRead
from app.core.constants import ALPACA_ORDERS_PAGE_LIMIT

CLOSED_STATUSES = {"filled", "canceled", "expired", "replaced", "rejected"}

# A notional (dollar-amount) order exactly as Alpaca returns it: qty is null.
NOTIONAL_ORDER_JSON: dict[str, Any] = {
    "id": "notional-1",
    "client_order_id": "manual-notional-1",
    "symbol": "MSFT",
    "qty": None,
    "notional": "100",
    "filled_qty": "0",
    "filled_avg_price": None,
    "side": "buy",
    "type": "market",
    "time_in_force": "day",
    "status": "accepted",
    "submitted_at": "2026-11-09T15:00:00.123456Z",
    "filled_at": None,
}


def make_order(
    order_id: str,
    *,
    symbol: str,
    status: str,
    submitted_at: datetime,
    qty: str | None = "1",
    notional: str | None = None,
) -> AlpacaOrderRead:
    return AlpacaOrderRead(
        id=order_id,
        client_order_id=f"client-{order_id}",
        symbol=symbol,
        qty=Decimal(qty) if qty is not None else None,
        notional=Decimal(notional) if notional is not None else None,
        side="buy",
        type="market",
        time_in_force="day",
        status=status,
        submitted_at=submitted_at.isoformat(),
    )


class FakeOrderBook:
    def __init__(self, orders: list[AlpacaOrderRead]):
        self.orders = orders
        self.calls: list[dict[str, Any]] = []

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        direction: str | None = None,
    ) -> list[AlpacaOrderRead]:
        self.calls.append(
            {"status": status, "limit": limit, "after": after, "until": until}
        )
        rows = [order for order in self.orders if self._matches(order, status, after, until)]
        rows.sort(key=self._submitted, reverse=direction != "asc")
        return rows[: min(limit, ALPACA_ORDERS_PAGE_LIMIT)]

    @staticmethod
    def _submitted(order: AlpacaOrderRead) -> datetime:
        assert order.submitted_at is not None
        return datetime.fromisoformat(order.submitted_at)

    def _matches(
        self,
        order: AlpacaOrderRead,
        status: str,
        after: datetime | None,
        until: datetime | None,
    ) -> bool:
        is_closed = order.status in CLOSED_STATUSES
        if status == "open" and is_closed:
            return False
        if status == "closed" and not is_closed:
            return False
        submitted = self._submitted(order)
        if after is not None and not submitted > after:
            return False
        if until is not None and not submitted < until:
            return False
        return True
