import httpx

from app.llm.base import LLMClient, LLMHealth


class OllamaClient(LLMClient):
    """LLMClient backed by a local Ollama server's chat API."""

    provider = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
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

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        return data.get("message", {}).get("content", "")

    async def health_check(self) -> LLMHealth:
        """Probe the local Ollama server via /api/tags; never raises on failure."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return LLMHealth(
                provider=self.provider,
                model=self._model,
                reachable=False,
                model_available=False,
                detail=f"Ollama unreachable at {self._base_url}: {exc}",
            )

        # Ollama reports installed models as e.g. "llama3.1:latest"; match on the
        # base name so OLLAMA_MODEL="llama3.1" resolves against the pulled tag.
        installed = {m.get("name", "") for m in data.get("models", [])}
        available = any(
            name == self._model or name.split(":", 1)[0] == self._model
            for name in installed
        )
        detail = (
            "ok"
            if available
            else (
                f"Server reachable but model '{self._model}' is not pulled. "
                f"Run: ollama pull {self._model}"
            )
        )
        return LLMHealth(
            provider=self.provider,
            model=self._model,
            reachable=True,
            model_available=available,
            detail=detail,
        )
