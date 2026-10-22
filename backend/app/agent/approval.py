"""Fresh risk validation and paper-order submission for an approved cycle proposal."""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import get_decrypted_alpaca_credentials
from app.broker.alpaca import AlpacaClient
from app.broker.gateway import submit_approved_order
from app.broker.reconcile import reconcile_order
from app.core.config import get_settings
from app.db.enums import (
    ApprovalStatus,
    BrokerOrderStatus,
    CycleStatus,
    DecisionAction,
    OrderSide,
    OrderType,
    TimeInForce,
)
from app.db.models import (
    AgentConfig,
    AgentCycle,
    BrokerOrder,
    CycleApproval,
    TradingAccount,
    utcnow,
)
from app.risk import daily_limits
from app.risk.kill_switch import is_kill_switch_enabled
from app.risk.market_hours import fetch_market_clock_for
from app.risk.policies import PolicyContext, evaluate_policies
from app.schemas import (
    AgentConfigRead,
    ApprovedOrder,
    DecisionProposal,
    MarketSnapshot,
    PolicyDecision,
    PolicyInput,
)


async def evaluate_and_submit_cycle_trade(
    db: AsyncSession,
    agent: AgentConfig,
    cycle: AgentCycle,
) -> tuple[PolicyDecision, BrokerOrder | None]:
    """Re-evaluate current paper-account risk and submit only policy-approved orders."""
    if not cycle.proposal_json:
        raise ValueError("Cycle has no proposal to execute")

    proposal = DecisionProposal.model_validate(cycle.proposal_json)
    if proposal.action == DecisionAction.HOLD:
        raise ValueError("HOLD proposals do not produce broker orders")

    settings = get_settings()
    paper_host = urlparse(settings.alpaca_paper_base_url).hostname
    if paper_host != "paper-api.alpaca.markets":
        raise ValueError("Order execution is restricted to the official Alpaca paper endpoint")
    account_result = await db.execute(
        select(TradingAccount).where(TradingAccount.id == agent.account_id)
    )
    account = account_result.scalar_one_or_none()
    if account is None or not account.is_connected or not account.is_paper:
        raise ValueError("A connected Alpaca paper account is required to execute orders")

    api_key, api_secret = await get_decrypted_alpaca_credentials(db, account.user_id)
    async with AlpacaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=settings.alpaca_paper_base_url,
    ) as client:
        account_state = await client.get_account()
        if account_state.last_equity <= 0:
            raise ValueError("Alpaca account did not provide a valid previous equity value")

        market = await client.get_market_snapshot(str(proposal.symbol))
        if not market.timestamp:
            raise ValueError("Latest market data has no timestamp; order execution is blocked")
        timestamp_text = market.timestamp.replace("Z", "+00:00")
        market_timestamp = datetime.fromisoformat(timestamp_text)
        if market_timestamp.tzinfo is None:
            raise ValueError("Market data timestamp is missing its timezone")
        data_age_seconds = (datetime.now(UTC) - market_timestamp.astimezone(UTC)).total_seconds()
        if data_age_seconds < 0:
            raise ValueError("Market data timestamp is in the future")

        positions = await client.get_positions()
        position = next((p for p in positions if p.symbol == str(proposal.symbol)), None)
        position_qty = (
            Decimal(str(position.qty))
            if position is not None and position.side.lower() == "long"
            else Decimal("0")
        )

        orders = await client.get_orders(status="all")
        orders_today = daily_limits.count_orders_today(orders)
        open_statuses = {
            "new",
            "accepted",
            "pending_new",
            "partially_filled",
            "held",
            "pending_replace",
        }
        open_order_symbols = {
            order.symbol for order in orders if order.status.lower() in open_statuses
        }

        clock = await fetch_market_clock_for(client)
        equity = account_state.equity
        daily_pnl_pct = daily_limits.daily_pnl_pct(equity, account_state.last_equity)
        input_data = PolicyInput(
            agent=AgentConfigRead(
                id=agent.id,
                name=agent.name,
                strategy_version=agent.strategy_version,
                cadence_minutes=agent.cadence_minutes,
                target_allocation=agent.target_allocation,
                max_symbol_allocation=agent.max_symbol_allocation,
                cash_reserve=agent.cash_reserve,
                daily_loss_limit=agent.daily_loss_limit,
                approval_mode=agent.approval_mode,
                enabled=agent.enabled,
            ),
            proposal=proposal,
            market=MarketSnapshot(
                symbol=proposal.symbol,
                current_price=market.latest_close,
                rsi_14=market.rsi_14,
                sma_20=market.sma_20,
            ),
            kill_switch_enabled=await is_kill_switch_enabled(db),
            orders_today=orders_today,
        )
        policy_context = PolicyContext(
            inp=input_data,
            equity=equity,
            cash=account_state.cash,
            position_qty=position_qty,
            open_order_symbols=open_order_symbols,
            market_open=clock.is_open,
            data_age_seconds=data_age_seconds,
            daily_pnl_pct=daily_pnl_pct,
            is_paper=account.is_paper,
            watchlist=set(agent.watchlist),
            max_orders_per_day=settings.max_orders_per_day,
        )
        decision = evaluate_policies(policy_context)
        if decision.policy_token is None:
            return decision, None

        approved_order = ApprovedOrder(
            decision_id=cycle.id,
            policy_token=decision.policy_token,
            # Deterministic, not random (Story 3.2.2 / Issue #73): if this
            # function is ever invoked twice for the same cycle - a double
            # -click race on approve, a retried scheduler tick - both calls
            # produce the SAME client_order_id, so the gateway's existing
            # dedup (#63) actually catches it instead of submitting two
            # real orders with two different random IDs.
            client_order_id=str(cycle.id),
            symbol=proposal.symbol,
            side=OrderSide(proposal.action.value),
            quantity=decision.final_quantity or proposal.quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        broker_order = await submit_approved_order(approved_order, client, db)
        return decision, broker_order


async def reconcile_uncertain_cycle(
    db: AsyncSession,
    agent: AgentConfig,
    cycle: AgentCycle,
) -> bool:
    """Resolve a cycle left uncertain by an interrupted broker-order submission."""
    result = await db.execute(
        select(BrokerOrder).where(BrokerOrder.decision_id == cycle.id)
    )
    broker_order = result.scalar_one_or_none()
    if broker_order is None:
        cycle.status = CycleStatus.FAILED
        cycle.failure_code = "ORDER_SUBMISSION_NOT_STARTED"
        cycle.failure_message = "No broker order was persisted for this cycle"
        cycle.completed_at = utcnow()
        await db.commit()
        return True

    account_result = await db.execute(
        select(TradingAccount).where(TradingAccount.id == agent.account_id)
    )
    account = account_result.scalar_one_or_none()
    if account is None or not account.is_connected or not account.is_paper:
        raise ValueError("A connected Alpaca paper account is required to reconcile orders")

    settings = get_settings()
    api_key, api_secret = await get_decrypted_alpaca_credentials(db, account.user_id)
    async with AlpacaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=settings.alpaca_paper_base_url,
    ) as client:
        broker_order = await reconcile_order(db, client, broker_order)

    if broker_order.status == BrokerOrderStatus.PENDING_SUBMISSION:
        broker_order.status = BrokerOrderStatus.REJECTED
        broker_order.rejection_reason = (
            "Order was not found at Alpaca during reconciliation; it was not resubmitted."
        )
        cycle.status = CycleStatus.FAILED
        cycle.failure_code = "ORDER_NOT_FOUND"
        cycle.failure_message = broker_order.rejection_reason
    elif broker_order.status == BrokerOrderStatus.RECONCILING:
        return False
    elif broker_order.status in {
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.CANCELED,
    }:
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "BROKER_ORDER_NOT_EXECUTED"
        cycle.failure_message = broker_order.rejection_reason
    else:
        cycle.status = CycleStatus.COMPLETED
        cycle.failure_code = None
        cycle.failure_message = None

    cycle.completed_at = utcnow()
    cycle.snapshot_json = {
        **(cycle.snapshot_json or {}),
        "execution_status": (
            "ORDER_NOT_FOUND"
            if broker_order.status == BrokerOrderStatus.REJECTED
            and cycle.failure_code == "ORDER_NOT_FOUND"
            else broker_order.status.value
        ),
    }
    await db.commit()
    return True


async def expire_pending_approvals(db: AsyncSession, agent_id: UUID) -> None:
    """Expire this agent's PENDING approvals past their expires_at.

    Shared by both entry points that can hit the active-cycle gate: the
    scheduler's tick (so a stale approval doesn't block the next scheduled
    run) and run_agent_cycle itself (so a manual trigger can clean up and
    proceed instead of hitting CycleOverlapError against a dead approval).
    """
    now = utcnow()
    result = await db.execute(
        select(CycleApproval, AgentCycle)
        .join(AgentCycle, CycleApproval.cycle_id == AgentCycle.id)
        .where(
            AgentCycle.agent_id == agent_id,
            CycleApproval.status == ApprovalStatus.PENDING,
            CycleApproval.expires_at <= now,
        )
        .with_for_update()
    )
    expired = result.all()
    for approval, cycle in expired:
        approval.status = ApprovalStatus.EXPIRED
        approval.acted_at = now
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "APPROVAL_EXPIRED"
        cycle.completed_at = now
        cycle.snapshot_json = {
            **(cycle.snapshot_json or {}),
            "execution_status": "APPROVAL_EXPIRED",
        }
    if expired:
        await db.commit()