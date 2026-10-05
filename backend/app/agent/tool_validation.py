"""Tool Schema Definition & Validation (story 2.1.1 / issue #26).

The LLM's only allowed output is a `DecisionProposal` (BUY, SELL, or HOLD on one of
the watchlist symbols) — the shared schema already defined in `app.schemas`, also used
by the Policy Engine (`PolicyInput`). This module is the strict validation boundary
between raw LLM output and that typed schema: malformed JSON or a schema violation is
never allowed to crash the agent loop. It's caught, logged, and raised as a
`ToolValidationError` the caller handles gracefully (e.g. HOLD, retry, or fail the cycle).
"""

import json
import logging

from pydantic import ValidationError

from app.schemas import DecisionProposal

logger = logging.getLogger(__name__)


class ToolValidationError(Exception):
    """Raised when the LLM's tool-call output is malformed or fails schema validation."""


def decision_proposal_json_schema() -> dict:
    """JSON Schema for DecisionProposal — register this with the LLM as its tool/function schema."""
    return DecisionProposal.model_json_schema()


def validate_decision_proposal(raw: str | bytes | dict) -> DecisionProposal:
    """Parse and validate the LLM's raw tool-call output into a DecisionProposal.

    Accepts a JSON string/bytes (as most LLM APIs return) or an already-parsed dict.
    Raises `ToolValidationError` — never an unhandled exception — for:
      - malformed JSON
      - a JSON value that isn't an object
      - a well-formed object that fails DecisionProposal's schema (bad action, symbol
        outside the watchlist, non-positive quantity, missing fields, etc.)
    """
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Malformed JSON in LLM tool call output: %s", exc)
        raise ToolValidationError(f"Malformed JSON: {exc}") from exc

    if not isinstance(data, dict):
        logger.error("LLM tool call output is not a JSON object: %r", data)
        raise ToolValidationError("Tool call output must be a JSON object")

    try:
        return DecisionProposal.model_validate(data)
    except ValidationError as exc:
        logger.error("LLM tool call failed schema validation: %s", exc)
        raise ToolValidationError(f"Schema validation failed: {exc}") from exc
