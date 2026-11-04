import logging
from typing import Any

from anthropic import APIError, AsyncAnthropic, NotFoundError

from app.llm.base import LLMClient, LLMHealth
from app.llm.decision_schema import DECISION_JSON_SCHEMA

logger = logging.getLogger(__name__)

# Thinking tokens count toward max_tokens, so leave generous headroom.
_MAX_TOKENS = 16000
_EFFORT = "medium"
_GENERATE_TIMEOUT = 120.0
_HEALTH_TIMEOUT = 10.0
_MAX_RETRIES = 2

# Models that support server-side refusal fallbacks (beta).
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)


class AnthropicClient(LLMClient):
    """LLMClient backed by Anthropic's Messages API (Claude)."""

    provider = "anthropic"

    def __init__(self, api_key: str | None, model: str) -> None:
        # Checked before any SDK object exists; the key is always passed explicitly so
        # the SDK never falls back to reading ANTHROPIC_API_KEY from the environment.
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        self._api_key = api_key
        self._model = model

    def _new_client(self, timeout: float) -> AsyncAnthropic:
        # One SDK client per call: Celery runs each task under a fresh asyncio.run, so a
        # client cached on this (lru_cached) instance would outlive its event loop.
        return AsyncAnthropic(api_key=self._api_key, timeout=timeout, max_retries=_MAX_RETRIES)

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Return Claude's JSON decision text for the prompt.

        ``temperature`` is accepted for interface compatibility but NOT sent: the
        Anthropic API version used by the SDK rejects sampling parameters
        (temperature/top_p/top_k). Output is constrained to DECISION_JSON_SCHEMA via
        structured output. Content is never raised on: a bad or empty answer is
        returned as-is (possibly "") so validate_decision_proposal can trigger the
        orchestrator's self-repair retry. An API failure (auth, rate limit, network)
        raises a RuntimeError naming only the error type and HTTP status.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": _EFFORT,
                "format": {"type": "json_schema", "schema": DECISION_JSON_SCHEMA},
            },
        }
        if system:
            kwargs["system"] = system
        if self._model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            async with self._new_client(_GENERATE_TIMEOUT) as client:
                response = await client.beta.messages.create(**kwargs)
        except APIError as exc:
            # The SDK's message embeds the provider's response body (error JSON, request
            # id). Log the diagnostics server-side and raise only the exception type and
            # HTTP status, matching health_check(); `from None` keeps the original (and
            # its body) out of the cycle's failure_message, the API response and the UI.
            status_code = getattr(exc, "status_code", None)
            logger.warning(
                "Anthropic request failed: %s status=%s request_id=%s model=%s",
                type(exc).__name__,
                status_code,
                getattr(exc, "request_id", None),
                self._model,
            )
            status_part = f" (HTTP {status_code})" if status_code is not None else ""
            raise RuntimeError(
                f"Anthropic API error: {type(exc).__name__}{status_part}"
            ) from None

        if response.stop_reason in ("refusal", "max_tokens"):
            category = getattr(getattr(response, "stop_details", None), "category", None)
            logger.warning(
                "Anthropic response stopped early: stop_reason=%s category=%s model=%s",
                response.stop_reason,
                category,
                self._model,
            )

        # Thinking blocks are ignored; only the answer text is returned.
        return "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        )

    async def health_check(self) -> LLMHealth:
        """Probe the Anthropic API by retrieving the model (no tokens); never raises."""
        try:
            async with self._new_client(_HEALTH_TIMEOUT) as client:
                await client.models.retrieve(self._model)
        except NotFoundError:
            return LLMHealth(
                provider=self.provider,
                model=self._model,
                reachable=True,
                model_available=False,
                detail=f"Model '{self._model}' not available to this API key",
            )
        except Exception as exc:
            # Exception type only: never str(exc), so no request detail or key can leak.
            return LLMHealth(
                provider=self.provider,
                model=self._model,
                reachable=False,
                model_available=False,
                detail=f"Anthropic API unreachable: {type(exc).__name__}",
            )

        return LLMHealth(
            provider=self.provider,
            model=self._model,
            reachable=True,
            model_available=True,
            detail="ok",
        )
