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

## Code quality: lint, types & pre-commit

Ruff (lint), mypy (static types), and pre-commit are installed with the `[dev]` extra.

```bash
ruff check app tests        # lint (E, F, I; line-length 100)
mypy                        # static type check (config in pyproject.toml)
```

mypy starts from a **lenient baseline** (`[tool.mypy]` in `pyproject.toml`) and is
tightened over time; a handful of modules with pre-existing type gaps are exempted via
`overrides` until they're annotated.

Install the git pre-commit hooks once so Ruff and mypy run automatically before each commit.
Normal commits only touch the files you changed:

```bash
pre-commit install              # from the repo root (or anywhere in the repo)
```

The hook config lives at `.pre-commit-config.yaml` (repo root) and wires Ruff lint + format
and mypy. CI enforces the same Ruff and mypy checks in `.github/workflows/lint.yml`.

> **Heads-up on `pre-commit run --all-files`:** the codebase has not yet been
> formatted with `ruff format`, so running the hooks across the whole repo will
> reformat a number of backend files in one pass (the `ruff-format` hook reports
> "Failed" when it rewrites files — re-running it then passes). That's expected;
> review and commit the formatting diff deliberately (ideally as its own commit)
> rather than mixing it into an unrelated change.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

On startup, the API creates missing tables and adds the `prompt_version` and
`prompt_revision` columns to existing `agent_cycles` tables.

- `GET /health` — liveness check, no DB access.
- `GET /health/db` — readiness check, runs `SELECT 1` through the async session to confirm connectivity.
- `POST /auth/register` — create a user (bcrypt-hashed password).
- `POST /auth/login` — returns a signed JWT (`access_token`) on valid credentials.
- `GET /auth/me` — protected route; requires `Authorization: Bearer <token>`, rejects unauthenticated requests with 401.
- `GET /market-data/bars` — protected route; latest OHLCV bars (default: 50 bars, 5-minute, for AAPL/NVDA/SPY)
  from Alpaca's Data API. Requires `ALPACA_API_KEY`/`ALPACA_API_SECRET` in `.env` (free paper-trading keys from
  https://app.alpaca.markets/); retries automatically on rate limiting (HTTP 429).
- `GET /llm/health` — reports the configured LLM provider/model (no API call, no cost).

### Scheduled Agent Cycles

Set `SCHEDULER_ENABLED=true` to start the in-process APScheduler worker with the API.
It checks enabled agents every 15 minutes by default (`SCHEDULER_INTERVAL_MINUTES`),
and only runs scheduled cycles when the Alpaca market clock reports the market open.
Each API scheduler enqueues scheduled work to Celery; workers execute the cycles using
Redis as both broker and result backend. Redis-backed interval deduplication prevents
multiple API instances from queueing the same schedule tick. The global
`KILL_SWITCH_ENABLED=true` setting skips all scheduled cycles. Both scheduler settings
default to safe/off values. Scheduled cycles require `ALPACA_API_KEY` and
`ALPACA_API_SECRET` to verify market hours; clock errors fail closed.
The existing manual `POST /agent/cycles/run` endpoint remains synchronous in this
initial increment; Celery currently handles recurring scheduled cycles.

For local development, start Redis and run a worker:

```powershell
docker compose up -d redis
cd backend
.venv\Scripts\celery.exe -A app.celery_app:celery_app worker --loglevel=info --pool=solo
```

Set `REDIS_URL` to the Redis URL accessible from the API and worker. In Docker Compose,
the API and worker use `redis://redis:6379/0`; outside Compose the default is
`redis://localhost:6379/0`. `docker compose up` starts Redis and a Celery worker along
with the rest of the stack. Cycle, approval, and broker-order records remain in PostgreSQL.

### LLM Client Factory

`app/llm/` gives a single provider-neutral interface (`LLMClient.generate(...)`) over OpenAI and a
local Ollama model. Switch providers with one `.env` variable — no orchestration code changes:

```bash
LLM_PROVIDER=ollama   # or "openai"
```

- `ollama`: requires a local [Ollama](https://ollama.com) server running (`OLLAMA_BASE_URL`, default
  `http://localhost:11434`) with the model pulled (`OLLAMA_MODEL`, default `llama3.1`).
- `openai`: requires `OPENAI_API_KEY` set; model via `OPENAI_MODEL` (default `gpt-4o-mini`).

#### Install & verify the local LLM

The project's `docker-compose.yml` runs Ollama as a service (reachable from the backend
container at `http://ollama:11434`). Pull the model and verify connectivity:

```bash
docker compose up -d ollama
docker compose exec ollama ollama pull llama3.1

# Active health probe (503 if unreachable):
curl "http://localhost:8000/llm/health?probe=true"

# Or run the standalone verifier (reachable + model available + a live generation):
python scripts/verify_llm.py
```

Running Ollama natively instead of via Compose? Set `OLLAMA_BASE_URL=http://localhost:11434`
for local dev (or `http://host.docker.internal:11434` from a container reaching the host).

### Encrypted Alpaca Credential Vault

`app/accounts/` lets a logged-in user connect their own Alpaca paper-trading credentials, encrypted
at rest (Fernet) via `CREDENTIAL_ENCRYPTION_KEY` — no endpoint ever returns the key/secret, encrypted
or plaintext.

- `POST /accounts/alpaca` — encrypt and store `{api_key, api_secret, is_paper}` for the current user
  (updates in place if already connected).
- `GET /accounts/alpaca` — connection status only (`provider`, `is_paper`, `is_connected`,
  `last_verified_at`); 404 if nothing is connected.
- `DELETE /accounts/alpaca` — revoke/remove the stored credentials.

`get_decrypted_alpaca_credentials(db, user_id)` is the runtime-decryption entry point a broker
integration calls to get the plaintext key/secret for an actual Alpaca API call — never logged or
returned to a client.

The `/api/v1/broker/*` endpoints consume the vault: each request builds its Alpaca client from the
authenticated user's decrypted credentials. If a user has no connected account, the broker falls
back to `X-Alpaca-*` headers / `ALPACA_*` in `.env` **only when `APP_ENV=development`**; otherwise
the request is rejected (400) and the broker is never called. If a connected account's ciphertext
cannot be decrypted (e.g. the key changed), the request fails with 500 rather than silently falling
back.

### Tool Schema Definition & Validation

`app/agent/tool_validation.py` is the strict boundary between raw LLM output and the shared
`DecisionProposal` schema (`app/schemas.py` — the same type the Policy Engine consumes). The
LLM's only allowed output is `DecisionProposal` (BUY/SELL/HOLD on a watchlist symbol).

- `validate_decision_proposal(raw)` — parses a JSON string/bytes or dict into a `DecisionProposal`.
  Malformed JSON or a schema violation never crashes the caller — it's logged and raised as
  `ToolValidationError` instead.
- `decision_proposal_json_schema()` — exports the JSON Schema, for registering with an LLM's
  function-calling/structured-output API.

## Tests

```bash
pytest                      # unit tests (no external services)
pytest -m "not integration" # what CI runs
OLLAMA_INTEGRATION=1 pytest -m integration   # live-Ollama test (needs the model pulled)
```
