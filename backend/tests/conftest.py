import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# Importing app.main registers the ORM models on Base.metadata (via app.db.models),
# so create_all below sees every table.

_settings = get_settings()


def _derive_test_database_url(database_url: str) -> str:
    """Append ``_test`` to the database name only, preserving host/query params.

    Naively doing ``f"{database_url}_test"`` corrupts URLs that carry a query
    string (e.g. ``?ssl=require`` becomes ``?ssl=require_test``). Parsing the URL
    and rewriting just the database segment keeps the rest intact.
    """
    url = make_url(database_url)
    return url.set(database=f"{url.database}_test").render_as_string(
        hide_password=False
    )


# Never run tests against the app's configured (dev/prod) database. Use a dedicated
# test database, overridable in CI via TEST_DATABASE_URL.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    _derive_test_database_url(_settings.database_url),
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, expire_on_commit=False)


async def _ensure_test_database_exists() -> None:
    """Create the isolated test database if it isn't there already.

    The suite provisions its own ``_test`` database rather than depending on the
    CI workflow to create it, so it runs the same way under any pipeline that only
    sets ``DATABASE_URL``. ``CREATE DATABASE`` can't run inside a transaction, so we
    connect to the server's default ``postgres`` maintenance database in AUTOCOMMIT
    mode. No-op when the database (or an explicit ``TEST_DATABASE_URL``) already exists.
    """
    url = make_url(TEST_DATABASE_URL)
    admin_engine = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await admin_engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _test_database_lifecycle():
    """Ensure the isolated test database exists, then dispose the pool at the end.

    Disposing matters because the module-scoped engine's pool would otherwise never
    be closed, leaking connections against a connection-limited Postgres in CI.
    """
    await _ensure_test_database_exists()
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def client():
    """Yields an HTTP client backed by an isolated, freshly-created test database.

    Tables are recreated before each test and dropped afterwards, so tests never
    depend on prior state and never touch the app's real database. The app's
    get_db dependency is overridden to use the test session, so no code path
    reaches the configured DATABASE_URL.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
