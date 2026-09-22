from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db

settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health() -> dict:
    """Liveness check — does not touch the database."""
    return {"status": "ok", "env": settings.app_env}


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict:
    """Readiness check — verifies a non-blocking round trip to PostgreSQL."""
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "result": result.scalar_one()}
