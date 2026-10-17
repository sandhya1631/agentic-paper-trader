"""Tests that only Alpaca's paper endpoint is accepted (Story 3.1.1 / Issue #70)."""

import pytest
from pydantic import ValidationError

from app.broker.alpaca import AlpacaClient
from app.core.config import Settings
from app.core.paper_guard import ensure_alpaca_paper_url

PAPER = "https://paper-api.alpaca.markets"
LIVE = "https://api.alpaca.markets"

ACCEPTED_URLS = [PAPER, f"{PAPER}/", f"{PAPER}/v2", f"{PAPER}/v2/"]

REJECTED_URLS = [
    LIVE,
    f"{LIVE}/v2",
    "http://paper-api.alpaca.markets",  # not https
    "https://paper-api.alpaca.markets.evil.com",  # look-alike host
    "https://evil.com/paper-api.alpaca.markets",  # paper host only in the path
    "https://paper-api.alpaca.markets@evil.com",  # real host is evil.com
    "https://user:pw@paper-api.alpaca.markets",  # credentials in the URL
    "https://paper-api.alpaca.markets:8443",  # unexpected port
    "https://paper-api.alpaca.markets/other",  # unexpected path
    "",
    "not a url",
]


# --- config: fails at startup ---


def test_live_endpoint_rejected_at_config():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpaca_paper_base_url=LIVE)


@pytest.mark.parametrize("url", ACCEPTED_URLS)
def test_paper_endpoint_accepted_at_config(url):
    settings = Settings(_env_file=None, alpaca_paper_base_url=url)
    assert settings.alpaca_paper_base_url == url


def test_default_settings_use_the_paper_endpoint():
    assert Settings(_env_file=None).alpaca_paper_base_url == PAPER


def test_env_var_override_to_live_endpoint_fails_at_startup(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", LIVE)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- the guard itself ---


@pytest.mark.parametrize("url", ACCEPTED_URLS)
def test_guard_accepts_paper_urls(url):
    assert ensure_alpaca_paper_url(url) == url


@pytest.mark.parametrize("url", REJECTED_URLS)
def test_guard_rejects_everything_else(url):
    with pytest.raises(ValueError):
        ensure_alpaca_paper_url(url)


def test_guard_error_does_not_echo_credentials():
    with pytest.raises(ValueError) as exc_info:
        ensure_alpaca_paper_url("https://user:secret-pw@api.alpaca.markets")

    assert "secret-pw" not in str(exc_info.value)


# --- AlpacaClient: defense in depth ---


def test_alpaca_client_refuses_non_paper_base_url():
    with pytest.raises(ValueError):
        AlpacaClient(api_key="k", api_secret="s", base_url=LIVE)


@pytest.mark.parametrize("url", ACCEPTED_URLS)
def test_alpaca_client_accepts_paper_url_and_normalizes_it(url):
    client = AlpacaClient(api_key="k", api_secret="s", base_url=url)

    assert client.base_url == PAPER


def test_alpaca_client_defaults_to_paper_endpoint():
    assert AlpacaClient(api_key="k", api_secret="s").base_url == PAPER