from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers ORM models on Base.metadata)
from app.db.base import Base
from app.db.session import engine, get_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No migrations story yet (Alembic isn't set up) — create tables on startup for local dev.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(auth_router)


@app.get("/health")
async def health() -> dict:
    """Liveness check — does not touch the database."""
    return {"status": "ok", "env": settings.app_env}


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict:
    """Readiness check — verifies a non-blocking round trip to PostgreSQL."""
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "result": result.scalar_one()}
