"""The strategy-comparison replay script (#188): the sample builder is walk-forward
and respects the requested window size, and the LLM factory follows LLM_PROVIDER."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "replay_compare.py"
_spec = importlib.util.spec_from_file_location("replay_compare", _PATH)
replay_compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_compare)

from app.core.config import Settings  # noqa: E402
from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.llm.openai_client import OpenAIClient  # noqa: E402
from app.market_data.alpaca_client import OHLCVBar  # noqa: E402


def _bars(count: int, start: float = 100.0) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            open=start + index,
            high=start + index + 1,
            low=start + index - 1,
            close=start + index,
            volume=1000,
        )
        for index in range(count)
    ]


def test_samples_respect_the_window_size():
    bars = _bars(60)
    samples = replay_compare.build_walk_forward_samples("AAPL", bars, window=50, max_samples=20)

    assert samples  # 60 bars, window 50 -> 10 possible samples
    for sample in samples:
        assert len(sample.bars_per_symbol["AAPL"]) == 50


def test_samples_have_no_look_ahead():
    bars = _bars(60)
    samples = replay_compare.build_walk_forward_samples("AAPL", bars, window=50, max_samples=20)

    for sample in samples:
        context_bars = sample.bars_per_symbol["AAPL"]
        held_close = sample.next_prices["AAPL"]
        context_closes = [bar.close for bar in context_bars]
        # The held-back outcome is never one of the bars the model sees, and every
        # context bar is older (lower close here, since closes rise monotonically).
        assert held_close not in context_closes
        assert all(close < held_close for close in context_closes)


def test_samples_are_capped_and_most_recent():
    bars = _bars(80)
    samples = replay_compare.build_walk_forward_samples("AAPL", bars, window=50, max_samples=5)

    assert len(samples) == 5
    # The most recent sample's outcome is the watchlist's very latest bar.
    assert samples[-1].next_prices["AAPL"] == bars[-1].close


def test_too_few_bars_produces_no_samples():
    bars = _bars(40)
    samples = replay_compare.build_walk_forward_samples("AAPL", bars, window=50, max_samples=20)

    assert samples == []


def test_model_window_tokens_uses_ollama_num_ctx_only_for_ollama():
    openai_settings = Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test")
    ollama_settings = Settings(_env_file=None, llm_provider="ollama", ollama_num_ctx=8192)

    assert replay_compare.model_window_tokens(openai_settings) == 128_000
    assert replay_compare.model_window_tokens(ollama_settings) == 8192


def test_llm_factory_builds_the_configured_providers_client_with_no_network_call():
    """#188: `--max-samples`/a comparison run uses whichever LLM_PROVIDER the
    deployment has configured; building the client must not touch the network."""
    openai_settings = Settings(
        _env_file=None, llm_provider="openai", openai_api_key="sk-test", openai_model="gpt-4o-mini"
    )
    ollama_settings = Settings(_env_file=None, llm_provider="ollama", ollama_model="llama3.1")

    openai_client = replay_compare.build_llm_factory(openai_settings)()
    ollama_client = replay_compare.build_llm_factory(ollama_settings)()

    assert isinstance(openai_client, OpenAIClient)
    assert isinstance(ollama_client, OllamaClient)
