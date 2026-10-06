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
- `GET /market-data/bars` — protected route; latest OHLCV bars (default: 50 bars, 5-minute, for AAPL/NVDA/SPY)
  from Alpaca's Data API. Requires `ALPACA_API_KEY`/`ALPACA_API_SECRET` in `.env` (free paper-trading keys from
  https://app.alpaca.markets/); retries automatically on rate limiting (HTTP 429).
- `GET /llm/health` — reports the configured LLM provider/model (no API call, no cost).

### LLM Client Factory

`app/llm/` gives a single provider-neutral interface (`LLMClient.generate(...)`) over OpenAI and a
local Ollama model. Switch providers with one `.env` variable — no orchestration code changes:

```bash
LLM_PROVIDER=ollama   # or "openai"
```

- `ollama`: requires a local [Ollama](https://ollama.com) server running (`OLLAMA_BASE_URL`, default
  `http://localhost:11434`) with the model pulled (`OLLAMA_MODEL`, default `llama3.1`).
- `openai`: requires `OPENAI_API_KEY` set; model via `OPENAI_MODEL` (default `gpt-4o-mini`).

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
pytest
```
