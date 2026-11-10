"""Provider-neutral JSON schema for the agent's decision (#154).

Shared by every provider that supports schema-constrained (structured) output, so the
Claude and OpenAI clients ask for exactly the same shape. This module deliberately
imports nothing: no provider SDK (so a missing `anthropic` package can never affect the
OpenAI/Ollama paths) and not `app.schemas` (so `app.llm` stays independent of it).
"""

from typing import Any

# Hand-written mirror of app.schemas.DecisionProposal; tests assert it cannot drift.
# Structured output makes the model's reply valid JSON of this shape, but
# validate_decision_proposal remains the authoritative check. Every property is required
# and additionalProperties is false, which OpenAI's strict mode also requires.
DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "quantity": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["symbol", "action", "quantity", "reasoning"],
    "additionalProperties": False,
}
