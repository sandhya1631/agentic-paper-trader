import logging

import pytest

from app.agent.tool_validation import (
    ToolValidationError,
    decision_proposal_json_schema,
    validate_decision_proposal,
)
from app.schemas import DecisionProposal

VALID_PROPOSAL = {
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 10,
    "reasoning": "RSI below 30 and price above SMA-20; oversold bounce setup.",
}


# --- Happy path ---


def test_validate_accepts_valid_json_string():
    result = validate_decision_proposal(
        '{"symbol": "AAPL", "action": "BUY", "quantity": 10, "reasoning": "test"}'
    )
    assert isinstance(result, DecisionProposal)
    assert result.symbol == "AAPL"
    assert result.action == "BUY"
    assert result.quantity == 10


def test_validate_accepts_already_parsed_dict():
    result = validate_decision_proposal(VALID_PROPOSAL)
    assert result.action == "BUY"
    assert result.symbol == "AAPL"


def test_validate_accepts_hold_action():
    hold = {**VALID_PROPOSAL, "action": "HOLD"}
    result = validate_decision_proposal(hold)
    assert result.action == "HOLD"


# --- Malformed JSON (never crashes, always ToolValidationError) ---


def test_validate_rejects_malformed_json():
    with pytest.raises(ToolValidationError):
        validate_decision_proposal("{not valid json")


def test_validate_rejects_non_object_json():
    with pytest.raises(ToolValidationError):
        validate_decision_proposal('["BUY", "AAPL"]')


def test_validate_logs_error_on_malformed_json(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ToolValidationError):
            validate_decision_proposal("not json at all")
    assert any("Malformed JSON" in record.message for record in caplog.records)


# --- Schema violations ---


def test_validate_rejects_invalid_action():
    bad = {**VALID_PROPOSAL, "action": "YOLO"}
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(bad)


def test_validate_rejects_symbol_outside_watchlist():
    bad = {**VALID_PROPOSAL, "symbol": "TSLA"}
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(bad)


def test_validate_rejects_non_positive_quantity():
    bad = {**VALID_PROPOSAL, "quantity": 0}
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(bad)


def test_validate_rejects_missing_required_field():
    bad = {"symbol": "AAPL", "action": "BUY", "quantity": 10}  # no reasoning
    with pytest.raises(ToolValidationError):
        validate_decision_proposal(bad)


def test_validate_logs_error_on_schema_violation(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ToolValidationError):
            validate_decision_proposal({**VALID_PROPOSAL, "action": "YOLO"})
    assert any("failed schema validation" in record.message for record in caplog.records)


# --- JSON Schema export (for LLM tool/function-call registration) ---


def test_decision_proposal_json_schema_has_required_fields():
    schema = decision_proposal_json_schema()
    assert schema["required"] == ["symbol", "action", "quantity", "reasoning"]
    assert set(schema["properties"].keys()) == {"symbol", "action", "quantity", "reasoning"}
