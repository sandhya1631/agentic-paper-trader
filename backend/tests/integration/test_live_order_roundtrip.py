"""Live paper order round-trip through the REAL broker gateway (#100).

PLACES A REAL ALPACA PAPER ORDER, then cancels it. Gated by its own flag,
ALPACA_INTEGRATION_ORDERS=1 (plus the env ALPACA_* paper keys), separate from the
read-only ALPACA_INTEGRATION tests, and never enabled in CI.

A 1-share DAY limit BUY at half the last price can't fill in a normal market; off-hours
it is queued for the next session. The test persists the BrokerOrder in the test
Postgres via submit_approved_order (dedup row first, then the POST), cancels it at
Alpaca, then checks reconcile_order adopts the broker's CANCELED status.

The ApprovedOrder is built here directly with a placeholder policy token: this test
exercises the gateway and broker, not the policy engine (which would deny off-hours,
P-003). In app code, an ApprovedOrder is only ever built by the approval path. If the
gateway starts verifying the token's HMAC (#123), build the order through the policy
engine instead.

Run it on a paper account the trading run doesn't use, or after the close: the order
counts toward P-012's daily cap and, while open, P-010 blocks that symbol. If cleanup
fails, a warning names the client_order_id to cancel in the Alpaca dashboard.
"""

import asyncio
import uuid
import warnings
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

import pytest
from integration_gates import require_alpaca_orders

from app.broker.alpaca import AlpacaClient
from app.broker.gateway import submit_approved_order
from app.broker.reconcile import reconcile_order
from app.core.config import get_settings
from app.db.enums import (
    ApprovalMode,
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    OrderSide,
    OrderType,
    TimeInForce,
    UserRole,
)
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.schemas import ApprovedOrder

pytestmark = pytest.mark.integration

SYMBOL = "AAPL"


async def _seed_cycle(db) -> uuid.UUID:
    """broker_orders.decision_id references agent_cycles, so the order needs a cycle."""
    user = User(email=f"it-order-{uuid.uuid4().hex[:8]}@example.com", password_hash="x",
                role=UserRole.OPERATOR)
    db.add(user)
    await db.flush()
    account = TradingAccount(user_id=user.id, provider="ALPACA", encrypted_api_key="n/a",
                             encrypted_api_secret="n/a", is_paper=True, is_connected=True)
    db.add(account)
    await db.flush()
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db.add(policy_set)
    await db.flush()
    agent = AgentConfig(account_id=account.id, policy_set_id=policy_set.id,
                        name="Integration order agent", watchlist=[SYMBOL],
                        strategy_version="S-001", approval_mode=ApprovalMode.OBSERVE_ONLY,
                        enabled=False, model_provider="none", model_name="none")
    db.add(agent)
    await db.flush()
    cycle = AgentCycle(agent_id=agent.id, trigger=CycleTrigger.MANUAL,
                       status=CycleStatus.EXECUTING, started_at=datetime.now(UTC))
    db.add(cycle)
    await db.commit()
    return cycle.id


async def _cancel_at_alpaca(client: AlpacaClient, client_order_id: str) -> int | None:
    """Cancel the order with this client_order_id at Alpaca; the DELETE status or None."""
    try:
        at_alpaca = await client.get_order_by_client_order_id(client_order_id)
        if at_alpaca is None:
            return None
        response = await client._get_client().delete(
            f"{client.base_url}/v2/orders/{at_alpaca.id}", headers=client.headers
        )
        status = response.status_code
    except Exception:  # cleanup must not hide the test's own failure
        status = None
    if status not in {200, 204, 422}:
        warnings.warn(
            f"Paper order client_order_id={client_order_id} may still be open: "
            "cancel it in the Alpaca dashboard",
            stacklevel=1,
        )
    return status


async def test_far_limit_order_round_trip_through_the_gateway(db_session):
    key, secret = require_alpaca_orders()
    cycle_id = await _seed_cycle(db_session)

    async with AlpacaClient(
        api_key=key, api_secret=secret, base_url=get_settings().alpaca_paper_base_url
    ) as client:
        snapshot = await client.get_market_snapshot(SYMBOL)
        far_price = (Decimal(str(snapshot.latest_close)) / 2).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        approved = ApprovedOrder(
            decision_id=cycle_id,
            policy_token="integration-test-placeholder",
            client_order_id=str(cycle_id),
            symbol=SYMBOL,
            side=OrderSide.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=far_price,
            time_in_force=TimeInForce.DAY,
        )

        try:
            order = await submit_approved_order(approved, client, db_session)
            assert order.broker_order_id, order.rejection_reason
            assert order.status in {BrokerOrderStatus.SUBMITTED, BrokerOrderStatus.ACCEPTED}
            assert order.filled_quantity == 0
            # Resubmitting the same cycle is deduplicated, never a second POST (#63).
            again = await submit_approved_order(approved, client, db_session)
            assert again.id == order.id
        finally:
            # Cancel whatever Alpaca holds for this client_order_id, even if the gateway
            # recorded the POST as failed (e.g. a timeout after Alpaca accepted it).
            # No assert here, so a cleanup problem never masks the real failure.
            cancel_status = await _cancel_at_alpaca(client, str(cycle_id))
        assert cancel_status in {200, 204, 422}, cancel_status

        for _ in range(10):  # the cancel is asynchronous at Alpaca
            order = await reconcile_order(db_session, client, order)
            if order.status == BrokerOrderStatus.CANCELED:
                break
            await asyncio.sleep(1)

    assert order.status == BrokerOrderStatus.CANCELED
    assert order.filled_quantity == 0
