import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# Importing app.main registers the ORM models on Base.metadata (via app.db.models),
# so create_all below sees every table.

_settings = get_settings()

# Never run tests against the app's configured (dev/prod) database. Use a dedicated
# test database, overridable in CI via TEST_DATABASE_URL.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    f"{_settings.database_url}_test",
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, expire_on_commit=False)


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
