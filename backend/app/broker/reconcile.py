"""Reconcile local BrokerOrder rows against Alpaca's truth (Story 1.3.2 / Issue #63).

If a submit times out or the process dies after persisting the PENDING_SUBMISSION
row but before (or during) the POST, the broker may or may not actually have the
order. Looking it up by client_order_id tells us which: found -> adopt the broker's
status; not found -> the POST never landed, so the gateway can safely (re)submit the
SAME client_order_id (Alpaca enforces client_order_id uniqueness on its side too).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.alpaca import AlpacaClient
from app.db.enums import BrokerOrderStatus
from app.db.models import BrokerOrder

_ALPACA_STATUS_MAP = {
    "new": BrokerOrderStatus.SUBMITTED,
    "accepted": BrokerOrderStatus.ACCEPTED,
    "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
    "filled": BrokerOrderStatus.FILLED,
    "canceled": BrokerOrderStatus.CANCELED,
    "expired": BrokerOrderStatus.CANCELED,
    "rejected": BrokerOrderStatus.REJECTED,
}


async def reconcile_order(
    db: AsyncSession, client: AlpacaClient, order: BrokerOrder
) -> BrokerOrder:
    """Refresh `order` from Alpaca's truth, looked up by its client_order_id.

    Leaves `order` in PENDING_SUBMISSION if Alpaca has no record of it — that is
    the caller's signal that it is safe to actually submit now.
    """
    alpaca_order = await client.get_order_by_client_order_id(order.client_order_id)

    if alpaca_order is None:
        return order

    order.broker_order_id = alpaca_order.id
    order.status = _ALPACA_STATUS_MAP.get(alpaca_order.status, BrokerOrderStatus.RECONCILING)
    order.filled_quantity = int(alpaca_order.filled_qty)
    await db.commit()
    await db.refresh(order)
    return order
