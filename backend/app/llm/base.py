from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMHealth:
    """Result of an active connectivity probe against an LLM backend.

    Unlike the cheap `/llm/health` echo, this reflects a real call to the
    provider: whether the backend is reachable and whether the configured model
    is actually available to serve requests.
    """

    provider: str
    model: str
    reachable: bool
    model_available: bool
    detail: str = ""


class LLMClient(ABC):
    """Provider-neutral interface for a single text completion.

    Concrete implementations (OpenAI, Ollama) hide the provider-specific
    request/response shape behind this one method, so orchestration code
    never branches on which provider is configured.
    """

    #: Short provider identifier, overridden by each concrete client.
    provider: str = "unknown"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Return the model's text completion for the given prompt."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> LLMHealth:
        """Actively probe the backend for reachability and model availability.

        Implementations MUST NOT raise on a connectivity/HTTP failure; they
        return ``LLMHealth(reachable=False, ...)`` with a human-readable
        ``detail`` instead, so callers (health endpoint, verify script) can
        report status without handling exceptions.
        """
        raise NotImplementedError
