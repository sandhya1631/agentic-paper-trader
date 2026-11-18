"""Opt-in gates for the integration tier (#100).

Every live test needs BOTH the ``integration`` marker (CI's default job deselects it
with ``-m "not integration"``) AND an explicit environment flag, so a bare local
``pytest`` never calls a live service. A test whose flag or keys are missing is
skipped with a reason that says what to set, so an unconfigured run stays green.

Keys are read through ``Settings`` (``backend/.env`` or the environment), never printed.
See docs/integration-tests.md.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings


def flag(name: str) -> bool:
    """True when the env flag is set to 1/true/yes."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def require_flag(name: str, what: str) -> None:
    if not flag(name):
        pytest.skip(f"set {name}=1 to run {what}")


def _alpaca_keys() -> tuple[str, str]:
    settings = get_settings()
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        pytest.skip("ALPACA_API_KEY and ALPACA_API_SECRET (paper) are not configured")
    return settings.alpaca_api_key, settings.alpaca_api_secret


def require_alpaca() -> tuple[str, str]:
    """Read-only Alpaca paper tests: ALPACA_INTEGRATION=1 plus the env ALPACA_* keys."""
    require_flag("ALPACA_INTEGRATION", "the read-only Alpaca paper tests")
    return _alpaca_keys()


def require_alpaca_orders() -> tuple[str, str]:
    """The order round-trip PLACES (then cancels) a paper order: its own flag."""
    require_flag(
        "ALPACA_INTEGRATION_ORDERS",
        "the paper order round-trip (it places and cancels a real paper order)",
    )
    return _alpaca_keys()


# Each provider has its own flag; the paid ones also need their key.
LLM_FLAGS = {
    "ollama": "OLLAMA_INTEGRATION",
    "openai": "OPENAI_INTEGRATION",
    "anthropic": "ANTHROPIC_INTEGRATION",
}


def require_llm(provider: str) -> None:
    provider = provider.strip().lower()
    if provider not in LLM_FLAGS:
        pytest.skip(f"LLM_PROVIDER {provider!r} has no integration flag")
    require_flag(LLM_FLAGS[provider], f"the live {provider} tests")
    settings = get_settings()
    if provider == "openai" and not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    if provider == "anthropic" and not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY is not configured")
