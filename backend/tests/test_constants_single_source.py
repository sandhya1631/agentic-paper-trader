"""Proves the formerly-duplicated constants now have exactly one source (Issue #94)."""

import inspect
from urllib.parse import urlparse

from app import schemas as app_schemas
from app.accounts import credential_vault
from app.agent import context, orchestrator, prompt
from app.agent import router as agent_router
from app.auth import schemas as auth_schemas
from app.broker import alpaca as broker_alpaca
from app.core import constants, paper_guard
from app.core.config import get_settings
from app.llm import factory as llm_factory
from app.market_data import router as market_data_router
from app.market_data.alpaca_client import DEFAULT_WATCHLIST as alpaca_client_watchlist
from app.risk import policies


def test_default_watchlist_is_the_same_object_everywhere():
    assert alpaca_client_watchlist is constants.DEFAULT_WATCHLIST
    assert market_data_router.DEFAULT_WATCHLIST is constants.DEFAULT_WATCHLIST


def test_active_cycle_statuses_is_the_same_object():
    assert orchestrator.ACTIVE_CYCLE_STATUSES is constants.ACTIVE_CYCLE_STATUSES


def test_alpaca_provider_is_the_same_object():
    assert credential_vault.ALPACA_PROVIDER is constants.ALPACA_PROVIDER
    assert agent_router.ALPACA_PROVIDER is constants.ALPACA_PROVIDER


def test_policy_version_is_the_same_object():
    assert policies.POLICY_VERSION is constants.POLICY_VERSION
    assert orchestrator.POLICY_VERSION is constants.POLICY_VERSION


def test_supported_providers_is_the_same_object():
    assert llm_factory.SUPPORTED_PROVIDERS is constants.SUPPORTED_PROVIDERS


def test_bcrypt_limit_matches():
    assert auth_schemas.BCRYPT_MAX_PASSWORD_BYTES == constants.BCRYPT_MAX_PASSWORD_BYTES


def test_default_strategy_version_matches_across_modules():
    context_default = inspect.signature(context.build_agent_context).parameters[
        "strategy_version"
    ].default
    assert context_default == constants.DEFAULT_STRATEGY_VERSION

    assert constants.DEFAULT_STRATEGY_VERSION in prompt.STRATEGY_REGISTRY
    assert (
        prompt.STRATEGY_REGISTRY[constants.DEFAULT_STRATEGY_VERSION].revision
        == constants.DEFAULT_PROMPT_REVISION
    )
    assert (
        app_schemas.AgentConfigRead.model_fields["strategy_version"].default
        == constants.DEFAULT_STRATEGY_VERSION
    )


def test_alpaca_paper_host_is_derived_from_the_one_base_url():
    """Issue #94's own expansion: the paper host existed as 3 separate literals
    (paper_guard.py, Settings.alpaca_paper_base_url, AlpacaClient's default) before
    this refactor. Now all three trace back to constants.ALPACA_PAPER_BASE_URL.
    """
    assert paper_guard.ALPACA_PAPER_HOST == urlparse(constants.ALPACA_PAPER_BASE_URL).hostname
    assert get_settings().alpaca_paper_base_url == constants.ALPACA_PAPER_BASE_URL

    client = broker_alpaca.AlpacaClient(api_key="k", api_secret="s")
    assert client.base_url == constants.ALPACA_PAPER_BASE_URL
