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
    # Largest watchlist PATCH /agent/config accepts (#141).
    max_watchlist_size: int
    # The watchlist a new agent starts with; the Market page shows it until the user has
    # an agent of their own (#165).
    default_watchlist: list[str]
    # Inclusive [min, max] for the agent's Ollama timeout and keep-alive (#203).
    llm_timeout_seconds_range: list[int]
    llm_keep_alive_minutes_range: list[int]
    # Providers a user may connect their own API key for (#191), in display order.
    llm_key_providers: list[str]
