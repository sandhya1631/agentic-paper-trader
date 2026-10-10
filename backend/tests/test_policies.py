from decimal import Decimal

from app.db.enums import DecisionAction, PolicyDecisionType, Symbol
from app.risk.policies import PolicyContext, evaluate_policies
from app.schemas import AgentConfigRead, DecisionProposal, MarketSnapshot, PolicyInput


def make_ctx(action=DecisionAction.BUY, qty=1, price="100", orders_today=0,
             kill_switch=False, **kw) -> PolicyContext:
    inp = PolicyInput(
        agent=AgentConfigRead(id="00000000-0000-0000-0000-000000000001", name="test"),
        proposal=DecisionProposal(symbol=Symbol.AAPL, action=action, quantity=qty,
                                  reasoning="test"),
        market=MarketSnapshot(symbol=Symbol.AAPL, current_price=Decimal(price)),
        kill_switch_enabled=kill_switch,
        orders_today=orders_today,
    )
    defaults = dict(equity=Decimal("100000"), cash=Decimal("50000"))
    defaults.update(kw)
    return PolicyContext(inp=inp, **defaults)


def failed_ids(decision) -> set[str]:
    return {e.policy_id for e in decision.evaluations if e.outcome == "FAIL"}


def test_valid_buy_is_allowed():
    d = evaluate_policies(make_ctx())
    assert d.decision == PolicyDecisionType.ALLOW
    assert d.final_quantity == 1


def test_over_allocation_denied():
    d = evaluate_policies(make_ctx(qty=200))  # 20k > 10% of 100k
    assert d.decision == PolicyDecisionType.DENY
    assert "P-007" in failed_ids(d)


def test_low_cash_denied():
    d = evaluate_policies(make_ctx(cash=Decimal("10")))
    assert "P-008" in failed_ids(d)


def test_market_closed_denied():
    assert "P-003" in failed_ids(evaluate_policies(make_ctx(market_open=False)))


def test_stale_data_denied():
    assert "P-004" in failed_ids(evaluate_policies(make_ctx(data_age_seconds=900)))


def test_short_sell_denied():
    d = evaluate_policies(make_ctx(action=DecisionAction.SELL, qty=5, position_qty=2))
    assert "P-009" in failed_ids(d)


def test_valid_sell_allowed():
    d = evaluate_policies(make_ctx(action=DecisionAction.SELL, qty=2, position_qty=5))
    assert d.decision == PolicyDecisionType.ALLOW


def test_kill_switch_denied():
    assert "P-002" in failed_ids(evaluate_policies(make_ctx(kill_switch=True)))


def test_not_paper_denied():
    assert "P-001" in failed_ids(evaluate_policies(make_ctx(is_paper=False)))


def test_open_order_conflict_denied():
    d = evaluate_policies(make_ctx(open_order_symbols={"AAPL"}))
    assert "P-010" in failed_ids(d)


def test_daily_loss_denied():
    d = evaluate_policies(make_ctx(daily_pnl_pct=Decimal("-0.03")))
    assert "P-011" in failed_ids(d)


def test_max_orders_denied():
    assert "P-012" in failed_ids(evaluate_policies(make_ctx(orders_today=10)))


def test_all_policies_are_evaluated_even_after_a_fail():
    d = evaluate_policies(make_ctx(market_open=False, kill_switch=True))
    assert {"P-002", "P-003"} <= failed_ids(d)
    assert len(d.evaluations) == 11
