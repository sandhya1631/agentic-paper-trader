from decimal import Decimal

from app.risk.allocation import check_maximum_allocation


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
