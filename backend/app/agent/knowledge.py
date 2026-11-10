"""Small curated strategy knowledge base for model context enrichment."""

from typing import Any

KNOWLEDGE_ENTRIES: tuple[dict[str, Any], ...] = (
    {
        "id": "risk-data-quality",
        "topic": "risk",
        "text": (
            "Missing, stale, or contradictory market/account data is a reason to "
            "HOLD. Never infer unavailable balances or prices."
        ),
    },
    {
        "id": "risk-position-sizing",
        "topic": "risk",
        "text": (
            "A proposal is not permission to trade. Respect paper-only execution, "
            "cash reserves, position limits, approvals, and policy validation."
        ),
    },
    {
        "id": "regime-uptrend",
        "topic": "trend",
        "text": (
            "Trend-following evidence is stronger when price is above both moving "
            "averages, the regime is UPTREND, and momentum confirms direction."
        ),
    },
    {
        "id": "regime-range",
        "topic": "mean-reversion",
        "text": (
            "Mean reversion is only appropriate in a RANGE regime. Avoid buying "
            "weakness during a confirmed DOWNTREND."
        ),
    },
    {
        "id": "breakout-volume",
        "topic": "breakout",
        "text": (
            "A breakout signal is more credible when relative volume is elevated "
            "and momentum confirms it; low-volume moves should be treated cautiously."
        ),
    },
    {
        "id": "capital-preservation",
        "topic": "capital-preservation",
        "text": (
            "When evidence is uncertain, preserving capital is preferable to "
            "forcing a trade. HOLD is a valid decision."
        ),
    },
)


def retrieve_knowledge(
    *,
    strategy_version: str,
    market: dict[str, Any],
    max_entries: int = 3,
) -> list[dict[str, str]]:
    """Return a small deterministic set of relevant curated knowledge entries."""
    topics: list[str] = ["risk"]
    if strategy_version == "S-004":
        topics.append("breakout")
    elif strategy_version == "S-005":
        topics.append("mean-reversion")
    elif strategy_version in {"S-003", "S-006"}:
        topics.append("trend")
    else:
        topics.append("capital-preservation")
    regimes = {
        item.get("regime")
        for item in market.values()
        if isinstance(item, dict)
    }
    if "UPTREND" in regimes:
        topics.append("trend")
    if "RANGE" in regimes:
        topics.append("mean-reversion")
    if any(
        isinstance(item, dict)
        and (item.get("relative_volume_20") or 0) >= 1.5
        for item in market.values()
    ):
        topics.append("breakout")
    if strategy_version == "S-007":
        topics.append("capital-preservation")

    selected: list[dict[str, str]] = []
    for topic in topics:
        for entry in KNOWLEDGE_ENTRIES:
            if entry["topic"] == topic and entry not in selected:
                selected.append(
                    {"id": entry["id"], "topic": entry["topic"], "text": entry["text"]}
                )
            if len(selected) >= max_entries:
                break
        if len(selected) >= max_entries:
            break
    return selected
