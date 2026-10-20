"""Broker gateway: the only path from an approved decision to a real order.

Story 1.3.1 / Issue #62.

Design notes:
  - Accepts ONLY ApprovedOrder (type-enforced) — a bare OrderCreate or raw
    DecisionProposal must never reach the broker through this function.
  - Persists the BrokerOrder row in PENDING_SUBMISSION (with client_order_id)
    BEFORE calling Alpaca, so a crash mid-submit is recoverable (ties into
    #63/#73 reconciliation) instead of leaving no record at all.
  - A resubmit with a client_order_id already recorded locally is NEVER sent to
    the broker again (#63): the existing row is reconciled against Alpaca's
    truth first, and only actually (re)submitted if Alpaca has no record of it.
  - Full cryptographic re-verification of policy_token against
    risk/policies._policy_token is issue #77's scope (it needs evaluated_at/
    action, which ApprovedOrder doesn't carry yet). Here we only guard against
    an empty token reaching the broker.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.alpaca import AlpacaClient
from app.broker.reconcile import reconcile_order
from app.broker.schemas import OrderCreate
from app.db.enums import BrokerOrderStatus
from app.db.models import BrokerOrder, utcnow
from app.schemas import ApprovedOrder


class ApprovedOrderRequiredError(Exception):
    """Raised when something other than a validated ApprovedOrder reaches the gateway."""


async def get_order_by_client_id(db: AsyncSession, client_order_id: str) -> BrokerOrder | None:
    """Look up a BrokerOrder by its unique client_order_id (Story 1.3.2 / Issue #63)."""
    result = await db.execute(
        select(BrokerOrder).where(BrokerOrder.client_order_id == client_order_id)
    )
    return result.scalar_one_or_none()


async def submit_approved_order(
    approved: ApprovedOrder,
    client: AlpacaClient,
    db: AsyncSession,
) -> BrokerOrder:
    """Submit an approved order to the broker, recording both order IDs.

    A client_order_id already recorded locally is never POSTed to the broker a
    second time — it is reconciled against Alpaca first (see #63).
    """
    if not isinstance(approved, ApprovedOrder):
        raise ApprovedOrderRequiredError(
            f"Broker gateway accepts only ApprovedOrder, got {type(approved).__name__}"
        )
    if not approved.policy_token:
        raise ApprovedOrderRequiredError("ApprovedOrder is missing a policy_token")

    broker_order = await get_order_by_client_id(db, approved.client_order_id)
    if broker_order is not None:
        broker_order = await reconcile_order(db, client, broker_order)
        if broker_order.status != BrokerOrderStatus.PENDING_SUBMISSION:
            # Alpaca already has this order on record - never resubmit it.
            return broker_order
        # Reconcile found no record at Alpaca: the earlier POST never landed,
        # so it's safe to actually submit now, reusing this same row.
    else:
        broker_order = BrokerOrder(
            decision_id=approved.decision_id,
            client_order_id=approved.client_order_id,
            status=BrokerOrderStatus.PENDING_SUBMISSION,
            symbol=approved.symbol.value,
            side=approved.side,
            requested_quantity=approved.quantity,
        )
        db.add(broker_order)
        await db.commit()
        await db.refresh(broker_order)

    order_create = OrderCreate(
        symbol=approved.symbol.value,
        qty=approved.quantity,
        side=approved.side,
        type=approved.order_type,
        time_in_force=approved.time_in_force,
        limit_price=approved.limit_price,
        client_order_id=approved.client_order_id,
    )

    try:
        alpaca_order = await client.submit_order(order_create)
    except httpx.HTTPError as exc:
        broker_order.status = BrokerOrderStatus.REJECTED
        broker_order.rejection_reason = str(exc)
        await db.commit()
        await db.refresh(broker_order)
        return broker_order

    broker_order.broker_order_id = alpaca_order.id
    broker_order.submitted_at = utcnow()
    broker_order.filled_quantity = int(alpaca_order.filled_qty)
    broker_order.average_fill_price = alpaca_order.average_fill_price
    order_status = alpaca_order.status.lower()
    if order_status == "filled":
        broker_order.status = BrokerOrderStatus.FILLED
    elif order_status in {"partially_filled", "partially-filled"}:
        broker_order.status = BrokerOrderStatus.PARTIALLY_FILLED
    elif order_status in {"rejected", "canceled", "cancelled"}:
        broker_order.status = (
            BrokerOrderStatus.REJECTED
            if order_status == "rejected"
            else BrokerOrderStatus.CANCELED
        )
    else:
        broker_order.status = BrokerOrderStatus.SUBMITTED
    await db.commit()
    await db.refresh(broker_order)
    return broker_order
