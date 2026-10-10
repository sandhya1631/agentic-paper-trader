from decimal import Decimal
from typing import Awaitable, Callable, TypeVar

from app.risk.allocation import AllocationResult, check_maximum_allocation

T = TypeVar("T")


async def execute_with_allocation_check(
    portfolio_equity: Decimal,
    current_exposure: Decimal,
    proposed_order_value: Decimal,
    submit_order: Callable[[], Awaitable[T]],
    max_symbol_allocation: Decimal = Decimal("0.10"),
) -> tuple[AllocationResult, T | None]:
    """Check maximum allocation before calling the broker."""

    result = check_maximum_allocation(
        portfolio_equity=portfolio_equity,
        current_exposure=current_exposure,
        proposed_order_value=proposed_order_value,
        max_symbol_allocation=max_symbol_allocation,
    )

    if not result.allowed:
        return result, None

    broker_result = await submit_order()
    return result, broker_result
