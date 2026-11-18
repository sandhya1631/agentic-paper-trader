import json
import logging
from typing import Any

import httpx

from app.llm.base import LLMClient, LLMHealth, LLMUnavailableError
from app.schemas import DecisionProposal

logger = logging.getLogger(__name__)


class OllamaClient(LLMClient):
    """LLMClient backed by a local Ollama server's chat API."""

    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.1,
        num_ctx: int = 4096,
        json_format: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._json_format = json_format

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": (
                    temperature if temperature is not None else self._temperature
                )
            },
        }
        payload["options"]["num_ctx"] = self._num_ctx
        if self._json_format:
            payload["format"] = DecisionProposal.model_json_schema()
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            # Never echo Ollama's error body or the server URL: report the type and status.
            status_code = exc.response.status_code
            logger.warning(
                "Ollama request failed: HTTPStatusError status=%s model=%s",
                status_code,
                self._model,
            )
            # A 404 usually means the model isn't pulled, or the base URL points elsewhere.
            hint = (
                f". Check that OLLAMA_MODEL is pulled (ollama pull {self._model}) and "
                "that OLLAMA_BASE_URL points at Ollama"
                if status_code == 404
                else ""
            )
            raise LLMUnavailableError(
                f"Ollama API error: HTTPStatusError (HTTP {status_code}){hint}"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL, json.JSONDecodeError) as exc:
            # A timeout's str() is empty and a connect error's names the URL, so name only
            # the type (e.g. ReadTimeout, ConnectError, or JSONDecodeError for a non-JSON
            # body).
            logger.warning(
                "Ollama request failed: %s model=%s", type(exc).__name__, self._model
            )
            raise LLMUnavailableError(f"Ollama API error: {type(exc).__name__}") from None

        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            logger.warning("Ollama response had no message object: model=%s", self._model)
            raise LLMUnavailableError("Ollama API error: unexpected response shape")
        return str(message.get("content") or "")

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
                # The probe is unauthenticated: name the error type, never the URL.
                detail=f"Ollama unreachable: {type(exc).__name__}",
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
