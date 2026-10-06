from dataclasses import dataclass
from decimal import Decimal


MAX_SYMBOL_ALLOCATION = Decimal("0.10")


@dataclass
class AllocationResult:
    allowed: bool
    current_exposure: Decimal
    proposed_order_value: Decimal
    projected_exposure: Decimal
    maximum_allowed: Decimal
    reason: str


def check_maximum_allocation(
    portfolio_equity: Decimal,
    current_exposure: Decimal,
    proposed_order_value: Decimal,
) -> AllocationResult:
    """Check that projected symbol exposure does not exceed 10%."""

    maximum_allowed = portfolio_equity * MAX_SYMBOL_ALLOCATION
    projected_exposure = current_exposure + proposed_order_value

    allowed = projected_exposure <= maximum_allowed

    if allowed:
        reason = "Projected symbol exposure is within the 10% allocation limit."
    else:
        reason = (
            f"Projected exposure {projected_exposure} exceeds "
            f"maximum allowed {maximum_allowed}."
        )

    return AllocationResult(
        allowed=allowed,
        current_exposure=current_exposure,
        proposed_order_value=proposed_order_value,
        projected_exposure=projected_exposure,
        maximum_allowed=maximum_allowed,
        reason=reason,
    )
