from dataclasses import dataclass
from decimal import Decimal


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
    max_symbol_allocation: Decimal = Decimal("0.10"),
) -> AllocationResult:
    """Check projected symbol exposure against the configured allocation limit."""

    maximum_allowed = portfolio_equity * max_symbol_allocation
    projected_exposure = current_exposure + proposed_order_value

    allowed = projected_exposure <= maximum_allowed

    if allowed:
        reason = "Projected symbol exposure is within the allocation limit."
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
