"""Per-agent Ollama request options (#203): the agent's override, else the server default.

`agent_configs.llm_timeout_seconds` / `llm_keep_alive_minutes` are NULL until an operator
sets them in Settings; NULL means "follow OLLAMA_TIMEOUT_SECONDS / OLLAMA_KEEP_ALIVE_MINUTES",
so a later change to the env default reaches every agent that never overrode it.
"""

from app.core.config import Settings
from app.db.models import AgentConfig


def effective_llm_timeout_seconds(agent: AgentConfig, settings: Settings) -> int:
    if agent.llm_timeout_seconds is not None:
        return agent.llm_timeout_seconds
    return settings.ollama_timeout_seconds


def effective_llm_keep_alive_minutes(agent: AgentConfig, settings: Settings) -> int:
    if agent.llm_keep_alive_minutes is not None:
        return agent.llm_keep_alive_minutes
    return settings.ollama_keep_alive_minutes
