"""Per-agent Ollama request options (#203): the agent's override, else the server default.

`agent_configs.llm_timeout_seconds` / `llm_keep_alive_minutes` are NULL until an operator
sets them in Settings; NULL means "follow OLLAMA_TIMEOUT_SECONDS / OLLAMA_KEEP_ALIVE_MINUTES",
so a later change to the env default reaches every agent that never overrode it.
"""

from app.core.config import Settings
from app.db.models import AgentConfig


def _override_or_default(override: int | None, default: int) -> int:
    return default if override is None else override


def effective_llm_timeout_seconds(agent: AgentConfig, settings: Settings) -> int:
    return _override_or_default(agent.llm_timeout_seconds, settings.ollama_timeout_seconds)


def effective_llm_keep_alive_minutes(agent: AgentConfig, settings: Settings) -> int:
    return _override_or_default(
        agent.llm_keep_alive_minutes, settings.ollama_keep_alive_minutes
    )


def stored_override(value: int, default: int) -> int | None:
    """What to store for a value set in Settings: the server default is stored as NULL,
    so setting it back to the default makes the agent follow the default again."""
    return None if value == default else value
