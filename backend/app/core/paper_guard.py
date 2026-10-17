"""Paper-trading guard (Story 3.1.1 / Issue #70).

Real-money orders must be impossible. The ONLY Alpaca trading endpoint we accept is
the paper one. A different URL (for example the live ``api.alpaca.markets``) fails
loudly, both when the settings load at startup and when an AlpacaClient is built.
"""

from urllib.parse import urlparse

ALPACA_PAPER_HOST = "paper-api.alpaca.markets"


def ensure_alpaca_paper_url(url: str) -> str:
    """Return ``url`` unchanged if it is Alpaca's paper endpoint, else raise ValueError.

    Accepted: https://paper-api.alpaca.markets, with an optional trailing slash and/or
    a trailing /v2. Everything else is refused (other hosts, http, extra ports, paths,
    credentials in the URL, look-alike hosts such as paper-api.alpaca.markets.evil.com).
    """
    parsed = urlparse(str(url).strip())
    host = parsed.hostname

    is_paper = (
        parsed.scheme == "https"
        and host == ALPACA_PAPER_HOST
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
        and parsed.path.rstrip("/") in ("", "/v2")
    )
    if not is_paper:
        # Only the host is echoed, never the full URL (it could contain credentials).
        raise ValueError(
            f"Only the Alpaca paper endpoint (https://{ALPACA_PAPER_HOST}) is allowed; "
            f"got host {host!r}. Real-money trading is disabled."
        )
    return url