from openai import AsyncOpenAI

from app.llm.base import LLMClient, LLMHealth


class OpenAIClient(LLMClient):
    """LLMClient backed by OpenAI's chat completions API."""

    provider = "openai"

    def __init__(self, api_key: str | None, model: str) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def health_check(self) -> LLMHealth:
        """Probe the OpenAI API by listing models; never raises on failure."""
        try:
            models = await self._client.models.list()
            model_ids = {m.id for m in getattr(models, "data", [])}
        except Exception as exc:
            return LLMHealth(
                provider=self.provider,
                model=self._model,
                reachable=False,
                model_available=False,
                detail=f"OpenAI API unreachable: {exc}",
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
