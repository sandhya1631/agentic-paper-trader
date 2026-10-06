from decimal import Decimal

import pytest

from app.risk.allocation import check_maximum_allocation
from app.risk.execution import execute_with_allocation_check


def test_allocation_within_limit():
    result = check_maximum_allocation(
        portfolio_equity=Decimal("10000"),
        current_exposure=Decimal("500"),
        proposed_order_value=Decimal("400"),
    )

    assert result.allowed is True
    assert result.projected_exposure == Decimal("900")
    assert result.maximum_allowed == Decimal("1000")


def test_allocation_exactly_at_limit():
    result = check_maximum_allocation(
        portfolio_equity=Decimal("10000"),
        current_exposure=Decimal("600"),
        proposed_order_value=Decimal("400"),
    )

    assert result.allowed is True
    assert result.projected_exposure == Decimal("1000")
    assert result.maximum_allowed == Decimal("1000")


def test_allocation_over_limit_is_rejected():
    result = check_maximum_allocation(
        portfolio_equity=Decimal("10000"),
        current_exposure=Decimal("700"),
        proposed_order_value=Decimal("400"),
    )

    assert result.allowed is False
    assert result.current_exposure == Decimal("700")
    assert result.proposed_order_value == Decimal("400")
    assert result.projected_exposure == Decimal("1100")
    assert result.maximum_allowed == Decimal("1000")
    assert "exceeds" in result.reason


def test_configurable_allocation_limit():
    result = check_maximum_allocation(
        portfolio_equity=Decimal("10000"),
        current_exposure=Decimal("300"),
        proposed_order_value=Decimal("300"),
        max_symbol_allocation=Decimal("0.05"),
    )

    assert result.allowed is False
    assert result.projected_exposure == Decimal("600")
    assert result.maximum_allowed == Decimal("500")


@pytest.mark.asyncio
async def test_broker_not_called_when_allocation_exceeds_limit():
    broker_called = False

    async def fake_submit_order():
        nonlocal broker_called
        broker_called = True
        return "order submitted"

    result, broker_result = await execute_with_allocation_check(
        portfolio_equity=Decimal("10000"),
        current_exposure=Decimal("700"),
        proposed_order_value=Decimal("400"),
        submit_order=fake_submit_order,
    )

    assert result.allowed is False
    assert result.projected_exposure == Decimal("1100")
    assert result.maximum_allowed == Decimal("1000")
    assert broker_result is None
    assert broker_called is False
