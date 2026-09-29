from abc import ABC, abstractmethod


class LLMClient(ABC):
    """Provider-neutral interface for a single text completion.

    Concrete implementations (OpenAI, Ollama) hide the provider-specific
    request/response shape behind this one method, so orchestration code
    never branches on which provider is configured.
    """

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
