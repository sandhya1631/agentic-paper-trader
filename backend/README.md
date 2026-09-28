# Backend

FastAPI skeleton connected to PostgreSQL via async SQLAlchemy (2.0 style, `asyncpg` driver), so all DB operations are non-blocking.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"

cp .env.example .env        # then fill in DATABASE_URL / JWT_SECRET
```

Create the database (once, using `psql` or any Postgres client):

```sql
CREATE DATABASE agentic_paper_trader;
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- `GET /health` — liveness check, no DB access.
- `GET /health/db` — readiness check, runs `SELECT 1` through the async session to confirm connectivity.
- `POST /auth/register` — create a user (bcrypt-hashed password).
- `POST /auth/login` — returns a signed JWT (`access_token`) on valid credentials.
- `GET /auth/me` — protected route; requires `Authorization: Bearer <token>`, rejects unauthenticated requests with 401.

## Tests

The test suite runs against a **dedicated, isolated test database** so it never
touches your dev/prod data. By default it uses the app's `DATABASE_URL` with `_test`
appended to the database name (e.g. `agentic_paper_trader_test`); override this with
the `TEST_DATABASE_URL` environment variable (CI sets it explicitly).

Create the test database once:

```sql
CREATE DATABASE agentic_paper_trader_test;
```

Then run:

```bash
pytest
```

The suite drops and recreates its tables around each test, so it leaves no residue.
