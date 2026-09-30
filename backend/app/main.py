from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import router as auth_router
from app.broker.router import router as broker_router
from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers ORM models on Base.metadata)
from app.db.base import Base
from app.db.session import engine, get_db

settings = get_settings()


tags_metadata = [
    {
        "name": "Authentication",
        "description": (
            "User registration, authentication, JWT token issuance, and user profile management."
        ),
    },
    {
        "name": "Broker Integration",
        "description": (
            "Alpaca paper trading endpoints for account summary, positions, orders, "
            "and technical indicators."
        ),
    },
    {
        "name": "Health",
        "description": "Application liveness and database readiness health check endpoints.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No migrations story yet (Alembic isn't set up) — create tables on startup for local dev.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Agentic Paper Trader API",
    description="""
### Interactive Endpoint Testing & API Documentation

Welcome to the **Agentic Paper Trader API** interactive Swagger documentation.

#### How to test protected endpoints:
1. **Register** a user via `POST /auth/register` or **Login** via `POST /auth/login`.
2. Click the green **Authorize** button at the top right of this page:
   - **OAuth2 Password Form**: Enter email in **username** and password into **password**.
   - **HTTP Bearer**: Alternatively, paste your raw JWT `access_token` string into the Bearer field.
3. Test protected routes like `GET /auth/me` and `GET /api/v1/broker/*` directly from your browser!
""",
    version="1.0.0",
    openapi_tags=tags_metadata,
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "list",
        "filter": True,
    },
    lifespan=lifespan,
)

# Without this, the browser blocks the Next.js frontend (localhost:3000) from
# calling this API (localhost:8000) entirely — curl/pytest never hit this
# since CORS is enforced by browsers, not the server-to-server request itself.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(broker_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root():
    """Redirect root path to interactive Swagger UI documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Health"], summary="Application liveness check")
async def health() -> dict:
    """Liveness check — does not touch the database."""
    return {"status": "ok", "env": settings.app_env}


@app.get("/health/db", tags=["Health"], summary="Database readiness check")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict:
    """Readiness check — verifies a non-blocking round trip to PostgreSQL."""
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "result": result.scalar_one()}

