import logging

from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from app.llm.base import LLMClient, LLMHealth
from app.llm.decision_schema import DECISION_JSON_SCHEMA

logger = logging.getLogger(__name__)

_GENERATE_TIMEOUT = 120.0
_HEALTH_TIMEOUT = 10.0
_MAX_RETRIES = 2

# Structured output (strict json_schema): the reply is valid JSON of the decision shape,
# so code fences or prose can no longer burn the orchestrator's one self-repair retry.
# validate_decision_proposal remains the authoritative check. Requires a model that
# supports structured output (the default gpt-4o-mini does); others get HTTP 400.
_RESPONSE_FORMAT: ResponseFormatJSONSchema = {
    "type": "json_schema",
    "json_schema": {
        "name": "decision_proposal",
        "schema": DECISION_JSON_SCHEMA,
        "strict": True,
    },
}


class OpenAIClient(LLMClient):
    """LLMClient backed by OpenAI's chat completions API."""

    provider = "openai"

    def __init__(self, api_key: str | None, model: str) -> None:
        # Checked before any SDK object exists; the key is always passed explicitly so
        # the SDK never falls back to reading OPENAI_API_KEY from the environment.
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        self._api_key = api_key
        self._model = model

    def _new_client(self, timeout: float) -> AsyncOpenAI:
        # One SDK client per call: Celery runs each task under a fresh asyncio.run, so a
        # client cached on this (lru_cached) instance would reuse connections that belong
        # to an event loop that has already closed.
        return AsyncOpenAI(api_key=self._api_key, timeout=timeout, max_retries=_MAX_RETRIES)

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Return the model's JSON decision text for the prompt.

        Output is constrained to DECISION_JSON_SCHEMA via structured output. Content is
        never raised on: a refusal, an empty answer or malformed text is returned as text
        (possibly "") so validate_decision_proposal can trigger the orchestrator's
        self-repair retry. An API failure (auth, rate limit, network, timeout) raises a
        RuntimeError naming only the error type and HTTP status.
        """
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            async with self._new_client(_GENERATE_TIMEOUT) as client:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    response_format=_RESPONSE_FORMAT,
                )
        except APIError as exc:
            # The SDK's message embeds the provider's response body (which can quote part
            # of the key) and its request id. Log diagnostics server-side and raise only
            # the exception type and HTTP status; `from None` keeps the original (and its
            # body) out of the cycle's failure_message, the API response and the UI.
            status_code = getattr(exc, "status_code", None)
            logger.warning(
                "OpenAI request failed: %s status=%s request_id=%s model=%s",
                type(exc).__name__,
                status_code,
                getattr(exc, "request_id", None),
                self._model,
            )
            status_part = f" (HTTP {status_code})" if status_code is not None else ""
            raise RuntimeError(f"OpenAI API error: {type(exc).__name__}{status_part}") from None

        if not response.choices:
            logger.warning("OpenAI response had no choices: model=%s", self._model)
            return ""
        choice = response.choices[0]
        if choice.message.refusal:
            # Never log the refusal text itself; the stop reason is enough to diagnose.
            logger.warning(
                "OpenAI refused the request: finish_reason=%s model=%s",
                choice.finish_reason,
                self._model,
            )
            return ""
        if choice.finish_reason in ("length", "content_filter"):
            logger.warning(
                "OpenAI response stopped early: finish_reason=%s model=%s",
                choice.finish_reason,
                self._model,
            )
        return choice.message.content or ""

    async def health_check(self) -> LLMHealth:
        """Probe the OpenAI API by listing models; never raises on failure."""
        try:
            async with self._new_client(_HEALTH_TIMEOUT) as client:
                models = await client.models.list()
            model_ids = {m.id for m in getattr(models, "data", [])}
        except Exception as exc:
            # Exception type only: never str(exc), so no response body or key can leak
            # (this detail is shown in the UI's header LLM pill).
            return LLMHealth(
                provider=self.provider,
                model=self._model,
                reachable=False,
                model_available=False,
                detail=f"OpenAI API unreachable: {type(exc).__name__}",
            )

        # If the listing is empty for any reason, don't claim the model is missing.
        available = self._model in model_ids if model_ids else True
        detail = "ok" if available else f"Model '{self._model}' not available to this account"
        return LLMHealth(
            provider=self.provider,
            model=self._model,
            reachable=True,
            model_available=available,
            detail=detail,
        )
