# Backend

FastAPI skeleton connected to PostgreSQL via async SQLAlchemy (2.0 style, `asyncpg` driver), so all DB operations are non-blocking.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"

cp .env.example .env        # then fill in DATABASE_URL if it differs from the default
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

## Tests

```bash
pytest
```
