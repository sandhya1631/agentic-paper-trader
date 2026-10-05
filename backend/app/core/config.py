from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "Agentic Paper Trader API"

    # Async SQLAlchemy connection string, e.g.:
    # postgresql+asyncpg://user:password@localhost:5432/agentic_paper_trader
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentic_paper_trader"
    )
    database_echo: bool = False

    # AuthSecurity (JWT) — dev-only default; override via .env for anything real.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agentic-paper-trader"
    jwt_expiration_minutes: int = 30

    # Origins allowed to call this API from a browser (the Next.js frontend).
    # Override via .env as a JSON array, e.g.: CORS_ALLOWED_ORIGINS=["http://localhost:3000"]
    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # Alpaca Trading API Credentials
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"

    # LLM API Credentials
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # LLM Client Factory — switch providers via .env, no code changes.
    llm_provider: str = "ollama"  # "openai" | "ollama"
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # Encrypted Alpaca Credential Vault — Fernet key, dev-only default.
    # Generate a real one for anything beyond local dev:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str = "YtmXdjfFYYQq-r4n2kcSNPUX03lIM2WfqvpM3YGC2k4="


@lru_cache
def get_settings() -> Settings:
    return Settings()
