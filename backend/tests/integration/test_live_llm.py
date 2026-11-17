"""Live LLM: a real generate() returns a schema-valid DecisionProposal (#100).

One case per provider, each behind its own flag (paid providers also need their key):
OLLAMA_INTEGRATION, OPENAI_INTEGRATION, ANTHROPIC_INTEGRATION. The client is built by
the production factory, and the input is the real S-001 prompt plus a context built by
the real context builder from synthetic bars, so no market or broker call is made.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from integration_gates import require_llm

from app.agent.context import build_agent_context
from app.agent.prompt import get_prompt_definition
from app.agent.tool_validation import validate_decision_proposal
from app.core.config import get_settings
from app.db.enums import DecisionAction
from app.llm.factory import create_llm_client
from app.market_data.alpaca_client import OHLCVBar

pytestmark = pytest.mark.integration


def _synthetic_bars(count: int = 60) -> list[OHLCVBar]:
    """Oldest-first 5-minute bars ending now, gently rising."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bars = []
    for i in range(count):
        close = 180.0 + i * 0.05
        bars.append(
            OHLCVBar(
                timestamp=(now - timedelta(minutes=5 * (count - 1 - i))).isoformat(),
                open=close - 0.02,
                high=close + 0.1,
                low=close - 0.1,
                close=close,
                volume=10_000 + i,
            )
        )
    return bars


@pytest.mark.parametrize("provider", ["ollama", "openai", "anthropic"])
async def test_live_generate_returns_a_valid_decision_proposal(provider: str):
    require_llm(provider)
    client = create_llm_client(get_settings().model_copy(update={"llm_provider": provider}))
    context = build_agent_context(
        symbols=["AAPL"],
        bars_per_symbol={"AAPL": _synthetic_bars()},
        account_data_status="UNAVAILABLE",
    )
    context["decision_target"] = "AAPL"
    system_prompt = get_prompt_definition("S-001").text

    raw = await client.generate(json.dumps(context, separators=(",", ":")), system=system_prompt)
    proposal = validate_decision_proposal(raw)

    assert proposal.symbol == "AAPL"
    assert proposal.action in set(DecisionAction)
    assert proposal.reasoning.strip()
    # The prompt mandates HOLD when the account is UNAVAILABLE; any other action is a
    # prompt-compliance finding worth seeing, but schema validity is what this asserts.
