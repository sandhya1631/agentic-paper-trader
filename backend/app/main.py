import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.router import router as accounts_router
from app.agent.orchestrator import recover_orphaned_cycles
from app.agent.router import router as agent_router
from app.agent.scheduler import add_agent_schedule_job, create_scheduler
from app.auth.router import router as auth_router
from app.broker.router import router as broker_router
from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers ORM models on Base.metadata)
from app.db.base import Base
from app.db.models import AgentConfig, AgentSchedule, TradingAccount, User
from app.db.schema import (
    ensure_agent_cycle_policy_columns,
    ensure_agent_cycle_prompt_columns,
    ensure_cycle_cancelled_status,
    ensure_kill_switch_event_scope_columns,
    ensure_policy_token_scrubbed,
    ensure_user_scheduler_column,
)
from app.db.session import AsyncSessionLocal, engine, get_db
from app.llm.base import LLMUnavailableError
from app.llm.router import router as llm_router
from app.market_data.router import router as market_data_router
from app.meta.router import router as meta_router

settings = get_settings()
logger = logging.getLogger(__name__)


tags_metadata = [
    {
        "name": "Authentication",
        "description": (
            "User registration, authentication, JWT token issuance, and user profile management."
        ),
    },
    {
        "name": "accounts",
        "description": (
            "Connect, check status of, and disconnect a user's Alpaca credentials. "
            "Secrets are encrypted at rest (Fernet) and never returned by any endpoint."
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
        await ensure_agent_cycle_prompt_columns(conn)
        await ensure_cycle_cancelled_status(conn)
        await ensure_agent_cycle_policy_columns(conn)
        await ensure_user_scheduler_column(conn)
        await ensure_kill_switch_event_scope_columns(conn)
        await ensure_policy_token_scrubbed(conn)
    # Close out cycles a previous process left in flight (a redeploy or crash mid-cycle,
    # #185) before any tick can run. Agents whose lock another live process holds are
    # skipped; their active-cycle gate recovers them later. Never blocks startup.
    try:
        async with AsyncSessionLocal() as db:
            await recover_orphaned_cycles(db)
    except Exception:
        logger.exception("Startup cycle recovery failed; the active-cycle gate will retry")
    scheduler = create_scheduler(
        settings,
        include_global_job=settings.scheduler_enabled,
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentSchedule)
            .join(AgentConfig, AgentSchedule.agent_id == AgentConfig.id)
            .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
            .join(User, TradingAccount.user_id == User.id)
            .where(
                AgentSchedule.enabled.is_(True),
                AgentConfig.enabled.is_(True),
                User.scheduler_enabled.is_(True),
            )
        )
        for schedule in result.scalars().all():
            add_agent_schedule_job(
                scheduler,
                schedule.id,
                schedule.interval_minutes,
            )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        app.state.scheduler = None


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
app.include_router(accounts_router)
app.include_router(broker_router, prefix="/api/v1")

app.include_router(llm_router)
app.include_router(market_data_router)
app.include_router(agent_router)
app.include_router(meta_router)


@app.exception_handler(LLMUnavailableError)
async def llm_unavailable_handler(_request: Request, exc: LLMUnavailableError) -> JSONResponse:
    """An unavailable LLM is a 503 on any route (#160), e.g. POST /agent/replay, where it
    can come from building the client in a dependency. The message is sanitized."""
    return JSONResponse(status_code=503, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 echoes the submitted input; for POST /accounts/llm that
    can be the user's API key (e.g. a missing `provider` echoes the whole body), so keep
    only each error's location, message and type there (#191). Other routes: default."""
    if request.url.path == "/accounts/llm":
        errors = [
            {"type": err.get("type"), "loc": err.get("loc"), "msg": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


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
