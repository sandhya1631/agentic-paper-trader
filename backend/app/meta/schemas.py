from app.schemas import APIModel


class ConstantsRead(APIModel):
    """Every enum/constant value the frontend needs to render, in one response.

    Keeps the frontend from hardcoding backend enum strings (e.g. "MANUAL",
    "COMPLETED") that would silently go stale if the backend ever renames or
    adds a value (Issue #139).
    """

    currency: str
    cycle_trigger: list[str]
    cycle_status: list[str]
    decision_action: list[str]
    policy_decision_type: list[str]
    approval_mode: list[str]
    approval_status: list[str]
