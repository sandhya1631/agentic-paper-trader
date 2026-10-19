"""Fresh risk validation and paper-order submission for an approved cycle proposal."""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import get_decrypted_alpaca_credentials
from app.broker.alpaca import AlpacaClient
from app.broker.gateway import submit_approved_order
from app.core.config import get_settings
from app.db.enums import DecisionAction, OrderSide, OrderType, TimeInForce
from app.db.models import AgentConfig, AgentCycle, BrokerOrder, TradingAccount
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
        today = datetime.now(UTC).date()
        orders_today = 0
        open_statuses = {
            "new",
            "accepted",
            "pending_new",
            "partially_filled",
            "held",
            "pending_replace",
        }
        open_order_symbols: set[str] = set()
        for order in orders:
            if order.status.lower() in open_statuses:
                open_order_symbols.add(order.symbol)
            if not order.submitted_at:
                continue
            submitted_text = order.submitted_at.replace("Z", "+00:00")
            submitted_at = datetime.fromisoformat(submitted_text)
            if submitted_at.tzinfo is None:
                raise ValueError("An Alpaca order timestamp is missing its timezone")
            if submitted_at.astimezone(UTC).date() == today:
                orders_today += 1

        clock = await fetch_market_clock_for(client)
        equity = account_state.equity
        daily_pnl_pct = (equity - account_state.last_equity) / account_state.last_equity
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
        )
        decision = evaluate_policies(policy_context)
        if decision.policy_token is None:
            return decision, None

        approved_order = ApprovedOrder(
            decision_id=cycle.id,
            policy_token=decision.policy_token,
            client_order_id=str(uuid4()),
            symbol=proposal.symbol,
            side=OrderSide(proposal.action.value),
            quantity=decision.final_quantity or proposal.quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        broker_order = await submit_approved_order(approved_order, client, db)
        return decision, broker_order
