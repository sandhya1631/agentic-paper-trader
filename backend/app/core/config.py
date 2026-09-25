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


@lru_cache
def get_settings() -> Settings:
    return Settings()
