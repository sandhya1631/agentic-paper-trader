"""The canonical form of an agent's watchlist (#179).

One helper for every reader of `agent_configs.watchlist`: the orchestrator (a manual
cycle and a scheduled sweep) and `GET`/`PATCH /agent/config`. Stored lists may predate
the PATCH validation (e.g. earlier SQL edits such as `["aapl", "AAPL"]`), so readers
normalize on the way out instead of rewriting the stored rows.
"""

from collections.abc import Iterable


def normalize_watchlist(watchlist: Iterable[object] | None) -> list[str]:
    """The watchlist in the canonical form the Ticker type produces: trimmed, upper-case,
    de-duplicated (first occurrence kept) and without blanks.

    A cycle collects market data and builds its context for each distinct symbol exactly
    once, one sweep never evaluates (or orders) the same symbol twice, and the Settings
    card compares its draft with the same list the agent actually uses.
    """
    symbols = (str(s).strip().upper() for s in (watchlist or []))
    return list(dict.fromkeys(s for s in symbols if s))
