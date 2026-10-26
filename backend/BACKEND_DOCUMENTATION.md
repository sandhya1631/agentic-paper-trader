# Agentic Paper Trader Backend

## 1. Scope and current implementation

This document describes the backend that currently exists in `backend/app`. It
does not describe the Next.js frontend, future roadmap items, or database
features that are only represented by response schemas.

The backend is a FastAPI application using:

- PostgreSQL through async SQLAlchemy 2.0 and `asyncpg`.
- Alpaca's paper-trading and market-data APIs.
- Ollama or OpenAI for the language-model decision proposal.
- APScheduler for in-process schedule timing.
- Celery and Redis for scheduled-cycle execution.
- Fernet encryption for user Alpaca credentials.
- Pydantic models for API, LLM, and risk-boundary validation.

The central design is:

> The LLM proposes a trade; deterministic Python code decides whether the
> proposal is allowed to reach the Alpaca paper broker.

The main application entry point is [main.py](app/main.py). The core decision
engine is [orchestrator.py](app/agent/orchestrator.py), and the deterministic
policy boundary is [policies.py](app/risk/policies.py).

---

## 2. Backend architecture

### 2.1 Layer diagram

```text
HTTP request or scheduler tick
              |
              v
       FastAPI routers
   auth / accounts / agent / broker
              |
              v
      Agent cycle orchestrator
              |
       +------+------+
       |             |
       v             v
 Market data     LLM provider
 bars + TA       Ollama/OpenAI
       |             |
       +------+------+
              v
    DecisionProposal validation
              |
              v
 Approval mode: HOLD / OBSERVE / REQUIRED / AUTO
              |
              v
 Fresh account + market + clock state
              |
              v
       Deterministic policies
              |
       +------+------+
       |             |
       v             v
   DENY          ALLOW + policy token
                         |
                         v
                Broker gateway
                         |
                         v
             Alpaca paper order + DB row
```

### 2.2 Runtime services

1. FastAPI starts and creates missing SQLAlchemy tables.
2. Startup adds three compatibility columns if an older database is being used:
   `agent_cycles.prompt_version`, `agent_cycles.prompt_revision`, and
   `users.scheduler_enabled`.
3. Enabled persisted schedules are loaded into APScheduler.
4. APScheduler only enqueues Celery work. Celery workers run the asynchronous
   cycle functions.
5. Redis stores Celery messages/results and scheduler tick history.
6. PostgreSQL stores users, encrypted account records, agent configuration,
   cycle history, approvals, schedules, runtime kill-switch state, and local
   broker-order state.

### 2.3 Docker Compose runtime

The current [docker-compose.yml](../docker-compose.yml) runs these backend
supporting services:

| Service | Role | Persistent volume |
|---|---|---|
| `postgres` | PostgreSQL database for application records | `postgres_data` mounted at `/var/lib/postgresql/data` |
| `redis` | Celery broker/result backend, scheduler history, and dispatch locks | `redis_data` mounted at `/data`; Redis AOF is enabled |
| `ollama` | Local LLM server when `LLM_PROVIDER=ollama` | `ollama_data` mounted at `/root/.ollama` for downloaded models |
| `backend` | FastAPI API and APScheduler process | No application-data volume; durable state is written to PostgreSQL/Redis |
| `celery-worker` | Executes scheduled Celery tasks | No application-data volume; uses the same PostgreSQL, Redis, Ollama, and credential settings |

The backend and Celery worker use the Compose service name `redis` for Redis
and `ollama` for Ollama. From a host development environment, the equivalent
defaults are typically `localhost:6379` and `localhost:11434`.

---

## 3. Configuration and startup behavior

Configuration is defined in [config.py](app/core/config.py).

Important settings:

| Setting | Current purpose/default |
|---|---|
| `APP_ENV` | Defaults to `development`; controls development-only Alpaca credential fallback. |
| `DATABASE_URL` | Async PostgreSQL URL. |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ISSUER`, `JWT_EXPIRATION_MINUTES` | JWT authentication settings. |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Server-level credentials used by market-data and scheduler clock checks. |
| `ALPACA_PAPER_BASE_URL` | Must be `https://paper-api.alpaca.markets` with optional `/v2`. |
| `LLM_PROVIDER` | `ollama` or `openai`; defaults to `ollama`. |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local LLM connection. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI connection. |
| `CREDENTIAL_ENCRYPTION_KEY` | Required Fernet key for user Alpaca credentials. |
| `SCHEDULER_ENABLED` | Enables the global APScheduler job; defaults to false. |
| `SCHEDULER_INTERVAL_MINUTES` | Global schedule interval; defaults to 15. |
| `REDIS_URL` | Celery and scheduler Redis connection. |
| `KILL_SWITCH_ENABLED` | Environment force-on kill switch; defaults to false. |
| `MAX_ORDERS_PER_DAY` | Runtime value for policy P-012; defaults to 10 and must be at least 1. |
| `DEFAULT_BAR_LIMIT` | Default number of market bars per symbol; defaults to 50. |
| `DEFAULT_TIMEFRAME_MINUTES` | Default market-bar interval; defaults to 5 minutes. |
| `MAX_RETRY_ATTEMPTS` | Maximum market-data request attempts; defaults to 3. |
| `RETRY_BACKOFF_SECONDS` | Initial rate-limit retry delay; defaults to 1 second and doubles per retry. |

`ensure_alpaca_paper_url()` in
[paper_guard.py](app/core/paper_guard.py) rejects live Alpaca URLs, HTTP,
credentials embedded in URLs, look-alike hosts, and unsupported paths. This
check happens while settings are loaded and again while an Alpaca client is
constructed.

`lifespan()` in [main.py](app/main.py):

1. Calls `Base.metadata.create_all`.
2. Runs the compatibility column helpers.
3. Creates APScheduler.
4. Loads enabled `AgentSchedule` rows for enabled users and agents.
5. Starts the scheduler.
6. Shuts the scheduler down when the application stops.

Fixed domain values that should not be environment-tunable are centralized in
[constants.py](app/core/constants.py). This includes the official Alpaca
endpoints and data feed, default watchlist, active-cycle statuses, strategy and
prompt versions, policy version, supported LLM providers, Alpaca provider name,
and bcrypt's 72-byte password limit. Runtime-tunable values such as market-data
limits, retry behavior, and daily order cap remain in `Settings`.

---

## 4. Database: what is currently stored

The ORM models are in [models.py](app/db/models.py). All UUID primary keys are
generated in Python. Timestamped models use UTC timestamps.

### 4.1 `users`

Stores application users:

- `id`
- `email` (unique)
- `password_hash` (bcrypt hash; plaintext is never stored)
- `role` (`VIEWER`, `OPERATOR`, `ADMIN`, or `AUDITOR`)
- `is_active`
- `scheduler_enabled`
- `created_at`, `updated_at`

The current authentication dependency checks that the JWT subject is a real
user. It does not currently reject an inactive user after lookup.

### 4.2 `trading_accounts`

Stores one Alpaca account per user/provider:

- `id`
- `user_id`
- `provider` (currently `ALPACA`)
- `encrypted_api_key`
- `encrypted_api_secret`
- `is_paper`
- `is_connected`
- `last_verified_at`
- `created_at`, `updated_at`

The database contains Fernet ciphertext, not the API key or secret. The
`TradingAccountRead` response intentionally excludes both encrypted columns.
`is_paper = true` is enforced by a database check constraint. A unique
`(user_id, provider)` constraint prevents duplicate connected rows.

### 4.3 `policy_sets`

Stores policy-set metadata:

- `id`
- `version` (unique)
- `limits_json`
- `is_active`
- `created_at`, `updated_at`

### 4.4 `agent_configs`

Stores how an agent is configured:

- `id`
- `account_id`
- `policy_set_id`
- `name`
- `watchlist` as JSON
- `strategy_version` (currently `S-001`)
- `cadence_minutes` (database constraint currently requires 15)
- `target_allocation`
- `max_symbol_allocation`
- `cash_reserve`
- `daily_loss_limit`
- `approval_mode` (`OBSERVE_ONLY`, `REQUIRED`, or `AUTO`)
- `enabled`
- `model_provider`
- `model_name`
- `version`
- `created_at`, `updated_at`

Constraints currently require the target allocation to be positive and no
larger than the maximum allocation, and the maximum symbol allocation to be at
most 10%. The `strategy_version` validator calls `get_system_prompt()` so an
unknown strategy version is rejected.

### 4.5 `agent_cycles`

This is the backend's main decision-history record. There is no separate
`decisions` table in the current MVP; `proposal_json` on this table is the
decision record.

Stored fields:

- `id`
- `agent_id`
- `trigger` (`MANUAL` or `SCHEDULED`)
- `status`
- `started_at`, `completed_at`
- `failure_code`, `failure_message`
- `snapshot_json`: sanitized market/account/position/order context plus
  execution metadata
- `prompt_version`
- `prompt_revision`
- `prompt_text`: exact rendered strategy prompt used
- `llm_raw_response`: exact response returned by the LLM, including an invalid
  first response if a repair attempt was needed
- `proposal_json`: validated `BUY`, `SELL`, or `HOLD` proposal
- `correlation_id`

The backend does **not** store the raw OHLCV bars in a dedicated table. The
cycle stores the summarized context and technical indicators in
`snapshot_json`.

### 4.6 `agent_schedules`

Stores per-user/per-agent recurring schedules:

- `id`
- `user_id`
- `agent_id`
- `interval_minutes`
- `enabled`
- `created_at`, `updated_at`

The `(user_id, agent_id)` pair is unique. The actual APScheduler job is
in-memory; this table is the durable source used to restore jobs at startup.

### 4.7 `system_runtime_controls`

Stores database-managed global runtime controls:

- `key` (currently the singleton key `"global"`)
- `kill_switch_enabled`
- `updated_by`
- `created_at`, `updated_at`

### 4.8 `cycle_approvals`

Stores human approval state for proposals when `approval_mode=REQUIRED`:

- `id`
- `cycle_id` (unique, one approval per cycle)
- `status`
- `proposal_hash`
- `requested_at`, `expires_at`, `acted_at`
- `actor_id`
- `comment`
- `policy_version`
- `policy_result_json`

The proposal hash protects against approving a proposal that has changed since
the approval was created. Approval requests expire after 24 hours.

### 4.9 `broker_orders`

Stores the local copy of each submitted or intended paper order:

- `id`
- `decision_id` (foreign key to `agent_cycles.id`)
- `client_order_id` (unique idempotency key)
- `broker_order_id` (Alpaca order id, once known)
- `status`
- `symbol`
- `side`
- `requested_quantity`
- `filled_quantity`
- `average_fill_price`
- `submitted_at`
- `rejection_reason`
- `created_at`, `updated_at`

The record is inserted as `PENDING_SUBMISSION` before the Alpaca POST. This
allows the reconciliation path to determine whether a timeout happened before
or after Alpaca accepted the order.

### 4.10 Data stored outside PostgreSQL

Redis stores:

- Celery broker messages and task results.
- Up to 100 scheduler tick summaries per global/schedule key.
- Scheduler tick history with a 30-day expiry.
- Short-lived interval deduplication keys used to prevent duplicate enqueueing
  across multiple API instances.

In the current Compose deployment, Redis runs with AOF enabled and its `/data`
directory is mounted to the `redis_data` Docker volume. Redis data therefore
survives container recreation, although Celery results, scheduler history, and
deduplication keys still have application-level expiry.

There is an `AuditEventRead` Pydantic response model, but there is currently no
corresponding `AuditEvent` SQLAlchemy model or persistence path. Therefore,
full audit events are not currently stored as a separate database table.

### 4.11 Storage ownership map

The following table shows where each important piece of data actually lives.
“Source of truth” means the system that owns the current value; “local copy”
means the backend stores a related record for history, safety, or
reconciliation.

| Data or state | Storage/system | Persistence | What is stored there | Source of truth or purpose |
|---|---|---|---|---|
| User identity and role | PostgreSQL `users` | Durable | Email, bcrypt hash, role, active flag, scheduler flag | Backend source of truth |
| User Alpaca credentials | PostgreSQL `trading_accounts` | Durable | Fernet-encrypted API key and secret plus connection metadata | Backend vault copy; plaintext exists only during an immediate broker call |
| Agent configuration | PostgreSQL `agent_configs` | Durable | Watchlist, strategy, risk thresholds, model, approval mode, enabled flag | Backend source of truth |
| Policy-set metadata | PostgreSQL `policy_sets` | Durable | Policy version, JSON limits, active flag | Stored configuration; current evaluator uses Python policy definitions |
| Agent cycle history | PostgreSQL `agent_cycles` | Durable | Status, trigger, timestamps, context snapshot, prompt, raw LLM response, proposal, errors | Backend decision/audit history |
| Market context used by a cycle | PostgreSQL `agent_cycles.snapshot_json` | Durable snapshot | Latest prices, indicators, account summary, positions, open-order summary, execution metadata | Historical snapshot only; not a raw market-data archive |
| Raw OHLCV bars | Alpaca Market Data API | External/managed by Alpaca | Five-minute bars requested by the backend | Alpaca is the source of truth; backend does not persist raw bars |
| Technical indicators | Runtime memory, then `agent_cycles.snapshot_json` | Temporary plus durable snapshot | RSI-14, SMA-20, SMA-50 and latest price | Calculated by backend from fetched bars; snapshot preserves what the cycle saw |
| Current account cash/equity | Alpaca Trading API | External/managed by Alpaca | Account balances and buying power | Alpaca is the source of truth; backend reads it fresh before execution |
| Current positions | Alpaca Trading API | External/managed by Alpaca | Open positions and quantities | Alpaca is the source of truth; backend does not maintain a portfolio ledger |
| Current broker order status | Alpaca Trading API | External/managed by Alpaca | Broker order id, status, fills, submitted time | Alpaca is the execution source of truth |
| Local order submission state | PostgreSQL `broker_orders` | Durable | Deterministic cycle-based client id, broker id, requested/fill quantities, local status, rejection reason | Local idempotency/reconciliation record linked to a cycle |
| Human approval state | PostgreSQL `cycle_approvals` | Durable | Pending/approved/rejected/expired state, proposal hash, actor, comment, expiry | Backend source of truth for approval workflow |
| Per-agent schedules | PostgreSQL `agent_schedules` | Durable | Owner, agent, interval, enabled flag | Durable schedule definition used to restore jobs |
| Running APScheduler jobs | API process memory | Temporary | Job objects, next-run times, in-process scheduler state | Runtime execution state; reconstructed from PostgreSQL after restart |
| Global kill switch | PostgreSQL `system_runtime_controls` | Durable | Database-managed enabled flag and last updater | Backend control state, overridden by environment force-on setting |
| Environment force-on settings | Process environment / `.env` | Configuration | Secrets, URLs, feature flags, kill-switch override | Deployment configuration; not application database data |
| Celery task messages | Redis via Celery broker | Temporary/expiry-based | Queued task name, arguments, retry/runtime metadata | Transport, not business-data storage |
| Celery task results | Redis via Celery result backend | Temporary, `result_expires=3600` | Serialized scheduler task return values and task status | Worker-result transport; PostgreSQL remains the business record |
| Scheduler tick history | Redis + `redis_data` volume in Compose | Up to 100 entries/key, 30-day expiry | Tick status, counts, reason, timestamps | Shared operational monitoring state |
| Scheduler dispatch locks | Redis | Short-lived, about two interval lengths | `SET NX` slot keys preventing duplicate enqueueing | Distributed coordination lock, not history |
| Scheduler fallback history | API process memory | Temporary | Recent tick summaries if Redis cannot be read/written | Best-effort fallback; lost on process restart |
| LLM prompt and response | LLM provider during request; PostgreSQL after cycle | Provider runtime plus durable cycle record | Prompt sent to Ollama/OpenAI and returned text | Provider performs generation; PostgreSQL preserves the cycle evidence |
| Downloaded Ollama models | Ollama container filesystem | Durable in Compose | Pulled model files | `ollama_data` Docker volume; not PostgreSQL or Redis |
| Logs and exceptions | Application process logging destination | Deployment-dependent | Operational messages and stack traces | Observability only; not a structured business-data store |

### 4.12 Storage boundaries in one request

For a scheduled trade, the storage sequence is:

```text
1. PostgreSQL:
   Read User, AgentConfig, TradingAccount, AgentSchedule, and kill-switch state.

2. Redis:
   Acquire the scheduler interval-deduplication key.
   Celery transports the scheduled task through Redis.

3. Alpaca:
   Read market bars, market clock, account balances, positions, and orders.

4. Process memory:
   Calculate indicators, build sanitized context, and hold the LLM request.

5. LLM provider:
   Generate the proposed BUY, SELL, or HOLD response.

6. PostgreSQL:
   Save AgentCycle context, prompt, raw response, proposal, and status.
   If approval is required, save CycleApproval.

7. Alpaca:
   If all policies pass, submit the paper order and let Alpaca own execution/fills.

8. PostgreSQL:
   Save BrokerOrder before submission and update it with the broker id/status.

9. Redis:
   Save the scheduler tick result and expire it according to Redis retention.
```

Celery is therefore not a separate database in this project. It is the task
execution framework; its broker and result backend are configured to use Redis.
Likewise, Alpaca is not a local database: it is the external authoritative
system for market data, paper-account balances, positions, and broker orders.

---

## 5. Data and decision flow

### 5.1 Authentication flow

1. `POST /auth/register` validates email/password.
2. Password length is checked against bcrypt's 72-byte limit.
3. The password is bcrypt-hashed and stored in `users.password_hash`.
4. `POST /auth/login` or `POST /auth/token` verifies the hash.
5. `issue_token()` creates a signed JWT containing `sub`, `exp`, and `iss`.
6. Protected endpoints call `get_current_user()`.
7. The dependency validates the JWT, converts `sub` to UUID, and loads the
   corresponding user.

### 5.2 Credential flow

1. `POST /accounts/alpaca` accepts the user's key and secret.
2. Each value is encrypted independently with Fernet.
3. Ciphertext is inserted or updated in `trading_accounts`.
4. Status endpoints return provider/paper/connected metadata only.
5. Broker execution decrypts credentials only immediately before constructing
   an `AlpacaClient`.
6. Credentials are not sent to the frontend, the LLM, or logs by design.

### 5.3 Market-data flow

1. `MarketDataCollector` asks Alpaca for up to `DEFAULT_BAR_LIMIT` bars
   (default 50) at `DEFAULT_TIMEFRAME_MINUTES`-minute intervals (default 5),
   using a 30-day lookback and the IEX feed. The bar limit, timeframe, retry
   count, and retry backoff are now environment-configurable through
   `Settings`.
2. Requests for the watchlist run concurrently.
3. HTTP 429 responses retry up to `MAX_RETRY_ATTEMPTS` attempts (default 3)
   with exponential backoff starting at `RETRY_BACKOFF_SECONDS` (default 1
   second).
4. Optional validation checks bar count, finite numeric values, positive
   prices, OHLC relationships, non-negative volume, and strictly increasing
   timestamps.
5. `calculate_indicators()` requires at least 50 closes and returns RSI-14,
   SMA-20, and SMA-50.
6. The orchestrator's current call uses `validate=False`; insufficient data is
   represented as missing indicators in the LLM context, which the prompt
   directs the model to treat as `HOLD`.

### 5.4 Context and LLM flow

`build_agent_context()` creates a sanitized dictionary containing:

- Strategy version and watchlist.
- Latest price, timestamp, RSI-14, SMA-20, SMA-50, and bar count per symbol.
- Account cash, equity, buying power, and daily P&L if supplied.
- Position symbol, quantity, market value, and unrealized P&L if supplied.
- Open-order id, symbol, side, quantity, and status if supplied.

It does not include credentials. The orchestrator adds the strategy and policy
versions, serializes the context as JSON, and sends it with the versioned
system prompt to the selected LLM client.

The current prompt (`S-001`, revision `1.0.0`) describes a technical-momentum
strategy:

- BUY when RSI/price trend/cash conditions align.
- SELL when RSI is overbought or price breaks below SMA-20 and a position exists.
- HOLD when data is missing, stale, conflicting, flat, or uncertain.

The prompt is guidance only. The Pydantic proposal validator and deterministic
policies are authoritative.

### 5.5 Proposal validation

The LLM may return only a `DecisionProposal`:

```json
{
  "symbol": "AAPL",
  "action": "BUY",
  "quantity": 10,
  "reasoning": "..."
}
```

Rules:

- Symbol must be one of `AAPL`, `NVDA`, or `SPY`.
- Action must be `BUY`, `SELL`, or `HOLD`.
- BUY/SELL quantity must be positive.
- HOLD quantity defaults to zero.
- Reasoning is required.

If JSON/schema validation fails, the orchestrator makes exactly one self-repair
LLM request. If that also fails, it does **not** create a guessed or safe HOLD
proposal. Instead it:

1. Marks the cycle `FAILED`.
2. Sets `failure_code` to `LLM_OUTPUT_INVALID`.
3. Saves the validation error in `failure_message`.
4. Saves the context, prompt metadata, and both the original and repair
   responses in the cycle record.
5. Leaves `proposal_json` empty.
6. Never creates an approval and never reaches policy evaluation or the broker.

---

## 6. Complete agent-cycle sequence

`run_agent_cycle()` in [orchestrator.py](app/agent/orchestrator.py) is the
main sequence.

### Step 1: Load and gate the agent

- Load `AgentConfig`.
- Expire old pending approvals for this agent.
- Search for a cycle in any active status.
- If an active cycle is `RECONCILING`, try to reconcile its broker order first.
- If an active cycle remains, raise `CycleOverlapError`.

This prevents two cycles for the same agent from running at the same time.

### Step 2: Create the cycle

Insert an `AgentCycle` with:

- `PENDING` status.
- Trigger (`MANUAL` or `SCHEDULED`).
- Strategy version/revision.
- Start time.
- Correlation id.

### Step 3: Collect market context

Set status to `COLLECTING`, fetch watchlist bars, calculate indicators where
there are at least 50 closes, and build the sanitized context.

A collector construction or bar-fetch problem is logged and leaves the cycle
with empty/missing market data, allowing the strategy's HOLD mandate to apply.

### Step 4: Ask the LLM

Set status to `REASONING`, load the provider-neutral client, and send the
context plus versioned system prompt.

### Step 5: Validate the proposal

Set status to `VALIDATING`. Validate the first response, perform exactly one
repair retry if needed. If both responses fail validation, end the cycle as
`FAILED` with `LLM_OUTPUT_INVALID`; do not create a proposal or send an order.

### Step 6: Persist decision evidence

Save:

- `snapshot_json`
- `prompt_version`
- `prompt_revision`
- `prompt_text`
- `llm_raw_response`
- `proposal_json`

### Step 7: Apply approval mode

| Condition | Cycle result | Broker call |
|---|---|---|
| Proposal is `HOLD` | `HOLD`, execution status `NO_TRADE_PROPOSED` | No |
| `OBSERVE_ONLY` | `COMPLETED`, execution status `OBSERVE_ONLY` | No |
| `REQUIRED` with BUY/SELL | `PENDING_APPROVAL`, approval row expires in 24 hours | No |
| `AUTO` with BUY/SELL | `EXECUTING`, then policy/broker result | Maybe |

### Step 8: Execute or wait

For AUTO, `evaluate_and_submit_cycle_trade()` refreshes the account, latest
market snapshot, positions, all orders, market clock, daily P&L, and kill-switch
state before policy evaluation. The proposal is never executed solely because
the LLM produced it.

For REQUIRED, approval endpoints perform the same fresh evaluation at approval
time. The account and market can therefore change between proposal time and
execution time.

---

## 7. How the decision changes over time

The decision changes in two separate ways:

### 7.1 LLM proposal changes

The LLM starts with current context and returns BUY, SELL, or HOLD. A malformed
response changes to:

1. A repaired LLM response, if the one allowed repair succeeds.
2. A terminal `FAILED` cycle with `LLM_OUTPUT_INVALID` if repair fails.

There is no fallback HOLD for invalid model output. This prevents the system
from treating an untrusted or incomplete response as an intentional decision.
On the failure path, `proposal_json` remains empty, no approval is created, and
the broker is never reached. A valid proposal is saved to `proposal_json` only
after schema validation succeeds.

### 7.2 Deterministic execution decision changes

For a BUY or SELL proposal, the final result can change as follows:

```text
BUY/SELL proposal
      |
      +-- HOLD? --------------------> HOLD / no trade
      |
      +-- OBSERVE_ONLY? ------------> COMPLETED / recorded only
      |
      +-- REQUIRED? ----------------> PENDING_APPROVAL
      |                                  |
      |                                  +--> human reject -> REJECTED
      |                                  +--> expiry -> REJECTED
      |                                  +--> human approve
      |
      +-- AUTO or approved
                 |
                 v
       Refresh live account/market state
                 |
                 v
          Run every policy
                 |
       +---------+----------+
       |                    |
       v                    v
   Any FAIL             All pass
   DENY                  ALLOW
   no broker call        signed policy token
       |                    |
       v                    v
   REJECTED           submit paper order
                            |
                 +----------+----------+
                 |                     |
                 v                     v
            known result          uncertain result
            COMPLETED/REJECTED    RECONCILING
```

The proposal quantity may be rejected by policy. In the current policy engine
there is no active resize policy; an allowed decision keeps the original
quantity.

---

## 8. Deterministic policy engine

`PolicyContext` in [policies.py](app/risk/policies.py) combines the proposal
with live broker state. `evaluate_policies()` always runs every policy so the
result contains complete evidence instead of stopping at the first failure.

| ID | Rule | Current behavior |
|---|---|---|
| P-001 | Paper-only broker | Requires `is_paper=True`. |
| P-002 | Kill switch | Fails if global/environment kill switch is enabled. |
| P-003 | Market open | Requires Alpaca clock to report open. |
| P-004 | Fresh data | Requires data age at most 600 seconds. |
| P-005 | Symbol allowlist | Requires symbol in agent watchlist. |
| P-007 | Maximum allocation | BUY projected symbol exposure must be at most configured max, default 10% of equity. |
| P-008 | Available cash | BUY cost must fit cash minus configured reserve. |
| P-009 | Sell ownership | SELL quantity cannot exceed owned long position. |
| P-010 | Open-order conflict | Rejects a symbol that already has an open order. |
| P-011 | Daily loss | Rejects when daily P&L is at or below the configured loss limit. |
| P-012 | Daily order count | Rejects when today's submitted-order count reaches `MAX_ORDERS_PER_DAY` (default 10). |

All policy failures produce `DENY`, no policy token, and no broker call. If all
policies pass, the result is `ALLOW`, includes the original quantity, and
contains an HMAC policy token. The gateway currently verifies that a token is
present; full token re-verification is not yet implemented there.

---

## 9. Order submission and reconciliation

The only cycle-to-broker path is `submit_approved_order()` in
[gateway.py](app/broker/gateway.py).

1. It requires an `ApprovedOrder` object.
2. It requires a non-empty policy token.
3. It checks the global kill switch immediately before submission.
4. It searches by `client_order_id`. For agent-cycle orders, this id is
   deterministically derived from the cycle UUID, so retries of the same cycle
   reuse the same idempotency key.
5. If a local row exists, it asks Alpaca for that client id before resubmitting.
6. If no local row exists, it inserts `PENDING_SUBMISSION` and commits first.
7. It creates the Alpaca order payload.
8. It POSTs to the paper endpoint.
9. It maps Alpaca status/fill data into `BrokerOrder`.

If the HTTP request is rejected, the local order becomes `REJECTED`. If the
process fails around submission, the cycle becomes `RECONCILING`. On a later
cycle attempt, `reconcile_uncertain_cycle()`:

- Finds the local broker row.
- Looks it up at Alpaca by `client_order_id`.
- Adopts the broker status if found.
- Re-submits only if Alpaca has no record and the local row is still pending.
- Marks the cycle completed/rejected/failed once the outcome is known.

This protects against duplicate orders after a timeout or process crash.

---

## 10. Approval flow

When a required-approval cycle creates a BUY/SELL proposal:

1. SHA-256 of sorted `proposal_json` is saved in `cycle_approvals`.
2. Status is `PENDING`.
3. Expiry is 24 hours after creation.
4. `GET /agent/approvals/pending` lists it.
5. Approve:
   - Locks and loads the approval owned by the user.
   - Rejects an already-acted-on approval.
   - Expires it if its time has passed.
   - Recomputes and constant-time-compares the proposal hash.
   - Records actor, timestamp, and comment.
   - Re-runs live policy evaluation and execution.
6. Reject:
   - Records actor, timestamp, and comment.
   - Marks the approval and cycle rejected.
7. Expiry:
   - Marks approval `EXPIRED`.
   - Marks the cycle `REJECTED` with `APPROVAL_EXPIRED`.

---

## 11. Scheduler and Celery flow

There are two schedule forms:

### Global schedule

With `SCHEDULER_ENABLED=true`, APScheduler runs a global interval job. Each
tick:

1. Checks the kill switch.
2. Finds enabled agents for users whose scheduler is enabled.
3. Excludes agents that have their own enabled `AgentSchedule`.
4. Checks the Alpaca market clock.
5. Runs each eligible agent cycle.
6. Counts successful, failed, and overlapping agents.
7. Stores a tick summary in Redis and in process memory fallback.

### Per-agent schedule

`POST /agent/scheduler/start` stores or updates `AgentSchedule`, enables the
user scheduler, and registers a job. The first run occurs after the interval;
it is not immediate.

`POST /agent/scheduler/{schedule_id}/stop` disables scheduling for the user and
removes that user's in-memory jobs. In-flight cycles are not forcibly stopped.

### Celery boundary

APScheduler calls `dispatch_agent_schedule()` or
`dispatch_scheduled_cycles()`. `_dispatch_once()` uses a Redis `SET NX` key
for the current interval slot, preventing multiple API instances from queuing
the same job. Celery workers call:

- `run_agent_schedule_task(schedule_id)`
- `run_scheduled_cycles_task()`

Each task creates an async execution context and disposes the SQLAlchemy
engine afterward.

---

## 12. API endpoint inventory

### Health and application

- `GET /` redirects to Swagger.
- `GET /health` is a no-database liveness check.
- `GET /health/db` executes `SELECT 1`.

### Authentication

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/token` (Swagger OAuth2 form)
- `GET /auth/me`

### Account vault

- `POST /accounts/alpaca`
- `GET /accounts/alpaca`
- `DELETE /accounts/alpaca`

### Market data and LLM

- `GET /market-data/bars`
- `GET /llm/health`
- `GET /llm/health?probe=true`

### Direct broker integration

Mounted under `/api/v1`:

- `GET /api/v1/broker/account`
- `GET /api/v1/broker/positions`
- `GET /api/v1/broker/snapshot/{symbol}`
- `POST /api/v1/broker/orders`
- `GET /api/v1/broker/orders`

The direct order endpoint checks the kill switch, but it does not create the
`BrokerOrder` cycle-linked record used by the agent gateway.

### Agent operations

- `GET /agent/kill-switch`
- `PUT /agent/kill-switch`
- `POST /agent/cycles/run`
- `GET /agent/cycles/{cycle_id}`
- `GET /agent/approvals/pending`
- `POST /agent/approvals/{approval_id}/approve`
- `POST /agent/approvals/{approval_id}/reject`
- `POST /agent/scheduler/start`
- `POST /agent/scheduler/{schedule_id}/stop`
- `GET /agent/scheduler/status`
- `GET /agent/scheduler/logs`

All protected routes use the current-user dependency. Kill-switch mutation and
read endpoints require the `ADMIN` role.

---

## 13. Complete function definition catalog

The following is a plain-language inventory of every function definition in
the backend application, including private helpers, model validators, and
async context-manager methods. Function locations are relative to `backend`.

### Application, database, and configuration

| Function | File | Simple responsibility |
|---|---|---|
| `lifespan` | `app/main.py` | Creates the schema, restores schedules, starts and stops APScheduler. |
| `root` | `app/main.py` | Redirects `/` to Swagger docs. |
| `health` | `app/main.py` | Returns API liveness without using the database. |
| `health_db` | `app/main.py` | Confirms PostgreSQL connectivity with `SELECT 1`. |
| `get_db` | `app/db/session.py` | Yields one async SQLAlchemy session per request. |
| `ensure_agent_cycle_prompt_columns` | `app/db/schema.py` | Adds prompt metadata columns to older databases. |
| `ensure_user_scheduler_column` | `app/db/schema.py` | Adds `users.scheduler_enabled` to older databases. |
| `utcnow` | `app/db/models.py` | Returns a timezone-aware UTC datetime. |
| `User.__init__` | `app/db/models.py` | Accepts legacy `hashed_password` input and maps it to `password_hash`. |
| `User.hashed_password` getter/setter | `app/db/models.py` | Provides compatibility access to `password_hash`. |
| `AgentConfig.validate_strategy_version` | `app/db/models.py` | Rejects an unknown registered strategy version. |
| `Settings._paper_endpoint_only` | `app/core/config.py` | Validates that configured Alpaca URL is paper-only. |
| `get_settings` | `app/core/config.py` | Returns cached environment configuration. |
| `ensure_alpaca_paper_url` | `app/core/paper_guard.py` | Refuses any non-official Alpaca paper URL. |

### Shared request, response, and decision schemas

| Function | File | Simple responsibility |
|---|---|---|
| `DecisionProposal.check_quantity_for_action` | `app/schemas.py` | Requires a positive quantity for BUY/SELL and permits zero for HOLD. |
| `PolicyDecision.decision_is_consistent` | `app/schemas.py` | Ensures ALLOW/DENY, failed policies, and policy-token presence agree. |
| `ApprovedOrder.price_matches_type` | `app/schemas.py` | Requires a limit price for LIMIT orders and forbids one for MARKET orders. |

### Authentication and account credentials

| Function | File | Simple responsibility |
|---|---|---|
| `_ensure_within_bcrypt_limit` | `app/auth/schemas.py` | Rejects passwords longer than 72 UTF-8 bytes. |
| `UserCreate.password_within_bcrypt_limit` | `app/auth/schemas.py` | Applies the password limit during registration validation. |
| `UserLogin.password_within_bcrypt_limit` | `app/auth/schemas.py` | Applies the same limit during login validation. |
| `hash_password` | `app/auth/security.py` | Creates a bcrypt password hash. |
| `verify_password` | `app/auth/security.py` | Checks a plaintext password against a bcrypt hash. |
| `issue_token` | `app/auth/security.py` | Signs a JWT with user id, issuer, and expiry. |
| `validate_token` | `app/auth/security.py` | Decodes and verifies a JWT. |
| `refresh_token` | `app/auth/security.py` | Validates an existing JWT and issues a new one. |
| `get_current_user` | `app/auth/dependencies.py` | Extracts a bearer token, validates it, and loads the user. |
| `register` | `app/auth/router.py` | Creates a user after duplicate-email checking. |
| `login` | `app/auth/router.py` | Authenticates JSON credentials and returns a JWT. |
| `login_for_access_token` | `app/auth/router.py` | Authenticates Swagger OAuth2 form credentials. |
| `me` | `app/auth/router.py` | Returns the authenticated user. |
| `_fernet` | `app/accounts/credential_vault.py` | Builds the Fernet cipher from configuration. |
| `encrypt_credential` | `app/accounts/credential_vault.py` | Encrypts one Alpaca secret. |
| `decrypt_credential` | `app/accounts/credential_vault.py` | Decrypts one stored Alpaca secret. |
| `get_alpaca_account` | `app/accounts/credential_vault.py` | Loads the user's encrypted Alpaca row. |
| `save_alpaca_credentials` | `app/accounts/credential_vault.py` | Encrypts and inserts or updates credentials. |
| `get_decrypted_alpaca_credentials` | `app/accounts/credential_vault.py` | Decrypts credentials for immediate broker use. |
| `delete_alpaca_credentials` | `app/accounts/credential_vault.py` | Deletes the user's stored credentials. |
| `connect_alpaca_account` | `app/accounts/router.py` | Protected endpoint that saves credentials. |
| `get_alpaca_account_status` | `app/accounts/router.py` | Returns connection metadata without secrets. |
| `disconnect_alpaca_account` | `app/accounts/router.py` | Protected endpoint that revokes credentials. |

### Agent context, prompts, validation, and orchestration

| Function | File | Simple responsibility |
|---|---|---|
| `build_agent_context` | `app/agent/context.py` | Builds the sanitized market/account context sent to the LLM. |
| `get_prompt_definition` | `app/agent/prompt.py` | Loads and renders a versioned strategy prompt. |
| `get_system_prompt` | `app/agent/prompt.py` | Returns only the rendered prompt text. |
| `decision_proposal_json_schema` | `app/agent/tool_validation.py` | Exports the Pydantic JSON schema for structured LLM output. |
| `validate_decision_proposal` | `app/agent/tool_validation.py` | Parses and validates raw LLM output as a proposal. |
| `run_agent_cycle` | `app/agent/orchestrator.py` | Runs the complete collect, reason, validate, persist, approve/execute sequence. |
| `evaluate_and_submit_cycle_trade` | `app/agent/approval.py` | Refreshes live state, evaluates policies, and submits an allowed order. |
| `reconcile_uncertain_cycle` | `app/agent/approval.py` | Resolves a cycle whose broker result was uncertain. |
| `expire_pending_approvals` | `app/agent/approval.py` | Expires old approvals and rejects their cycles. |

### Agent HTTP routes and scheduling

| Function | File | Simple responsibility |
|---|---|---|
| `get_kill_switch` | `app/agent/router.py` | Returns effective kill-switch state for an admin. |
| `update_kill_switch` | `app/agent/router.py` | Changes database kill-switch state for an admin. |
| `trigger_manual_cycle` | `app/agent/router.py` | Finds or provisions a default agent and runs a manual cycle. |
| `get_cycle_status` | `app/agent/router.py` | Loads an owned cycle and its approval. |
| `_job_id` | `app/agent/router.py` | Creates the APScheduler job id for a schedule. |
| `_schedule_read` | `app/agent/router.py` | Converts schedule and scheduler job state to an API response. |
| `_load_owned_approval` | `app/agent/router.py` | Locks and loads an approval owned by the current user. |
| `list_pending_approvals` | `app/agent/router.py` | Lists the user's pending proposals. |
| `approve_cycle_trade` | `app/agent/router.py` | Hash-checks, records, re-evaluates, and executes an approval. |
| `reject_cycle_trade` | `app/agent/router.py` | Records a human rejection and closes the cycle. |
| `start_agent_schedule` | `app/agent/router.py` | Persists/enables a schedule and registers its job. |
| `stop_agent_schedule` | `app/agent/router.py` | Disables the user's scheduling and removes jobs. |
| `get_scheduler_status` | `app/agent/router.py` | Reports jobs, schedules, recent cycles, and recent orders. |
| `get_scheduler_logs` | `app/agent/router.py` | Returns recent Redis-backed tick summaries. |
| `_tick_history_key` | `app/agent/scheduler.py` | Builds the Redis key for tick history. |
| `_new_redis_client` | `app/agent/scheduler.py` | Creates a short-timeout Redis client. |
| `get_last_tick_status` | `app/agent/scheduler.py` | Reads the newest tick from Redis or memory fallback. |
| `get_tick_history` | `app/agent/scheduler.py` | Reads recent tick summaries, newest first. |
| `_store_tick_status` | `app/agent/scheduler.py` | Persists and trims tick history in Redis. |
| `_fetch_market_clock` | `app/agent/scheduler.py` | Uses server Alpaca credentials to read the market clock. |
| `run_scheduled_cycles` | `app/agent/scheduler.py` | Performs one global or filtered scheduled tick. |
| `finish` | `app/agent/scheduler.py` | Builds, stores, and logs a tick summary. |
| `run_agent_schedule` | `app/agent/scheduler.py` | Runs a tick for one persisted schedule if still enabled. |
| `add_agent_schedule_job` | `app/agent/scheduler.py` | Adds/replaces an APScheduler interval job. |
| `_dispatch_once` | `app/agent/scheduler.py` | Uses Redis slot locking to deduplicate enqueueing. |
| `dispatch_agent_schedule` | `app/agent/scheduler.py` | Queues one per-agent Celery task. |
| `dispatch_scheduled_cycles` | `app/agent/scheduler.py` | Queues one global Celery task. |
| `create_scheduler` | `app/agent/scheduler.py` | Creates the APScheduler instance and optional global job. |
| `_run_async` | `app/agent/tasks.py` | Runs an async coroutine and disposes the DB engine. |
| `run_and_dispose` | `app/agent/tasks.py` | Inner cleanup wrapper used by `_run_async`. |
| `run_scheduled_cycles_task` | `app/agent/tasks.py` | Celery entry point for global scheduled cycles. |
| `run_agent_schedule_task` | `app/agent/tasks.py` | Celery entry point for one schedule. |

### LLM clients

| Function | File | Simple responsibility |
|---|---|---|
| `LLMClient.generate` | `app/llm/base.py` | Abstract provider-neutral text-generation contract. |
| `LLMClient.health_check` | `app/llm/base.py` | Abstract provider/model availability contract. |
| `create_llm_client` | `app/llm/factory.py` | Creates OpenAI or Ollama from settings. |
| `get_llm_client` | `app/llm/factory.py` | Returns a cached provider client. |
| `OllamaClient.__init__` | `app/llm/ollama_client.py` | Stores Ollama URL and model. |
| `OllamaClient.generate` | `app/llm/ollama_client.py` | Calls Ollama `/api/chat` without streaming. |
| `OllamaClient.health_check` | `app/llm/ollama_client.py` | Checks Ollama reachability and installed model. |
| `OpenAIClient.__init__` | `app/llm/openai_client.py` | Requires an API key and stores the model. |
| `OpenAIClient.generate` | `app/llm/openai_client.py` | Calls OpenAI chat completions. |
| `OpenAIClient.health_check` | `app/llm/openai_client.py` | Lists OpenAI models and checks availability. |
| `llm_health` | `app/llm/router.py` | Echoes configuration or performs an active LLM probe. |

### Alpaca broker integration

| Function | File | Simple responsibility |
|---|---|---|
| `_optional_decimal` | `app/broker/alpaca.py` | Converts optional API numbers to Decimal. |
| `AlpacaClient.__init__` | `app/broker/alpaca.py` | Configures a paper-only async Alpaca client. |
| `AlpacaClient._get_client` | `app/broker/alpaca.py` | Lazily creates/reuses an HTTP client. |
| `AlpacaClient.close` | `app/broker/alpaca.py` | Closes an HTTP client owned by the wrapper. |
| `AlpacaClient.__aenter__` | `app/broker/alpaca.py` | Supports async context-manager entry. |
| `AlpacaClient.__aexit__` | `app/broker/alpaca.py` | Supports async context-manager cleanup. |
| `AlpacaClient.get_account` | `app/broker/alpaca.py` | Fetches account cash/equity/buying-power data. |
| `AlpacaClient.get_positions` | `app/broker/alpaca.py` | Fetches open positions. |
| `AlpacaClient.get_bars` | `app/broker/alpaca.py` | Fetches raw market bars from Alpaca data API. |
| `AlpacaClient.get_market_snapshot` | `app/broker/alpaca.py` | Gets bars and computes current indicators. |
| `AlpacaClient.submit_order` | `app/broker/alpaca.py` | Sends a paper order and maps the response. |
| `AlpacaClient.get_orders` | `app/broker/alpaca.py` | Fetches paper order history. |
| `AlpacaClient.get_order_by_client_order_id` | `app/broker/alpaca.py` | Finds one Alpaca order by idempotency key. |
| `get_order_by_client_id` | `app/broker/gateway.py` | Finds one local `BrokerOrder`. |
| `submit_approved_order` | `app/broker/gateway.py` | Enforces gateway checks, persists, submits, and maps order state. |
| `reconcile_order` | `app/broker/reconcile.py` | Updates a local order from Alpaca truth. |
| `get_alpaca_client` | `app/broker/router.py` | Resolves encrypted user credentials or dev-only fallback. |
| `get_account_summary` | `app/broker/router.py` | Returns Alpaca account data. |
| `get_open_positions` | `app/broker/router.py` | Returns Alpaca positions. |
| `get_market_snapshot` | `app/broker/router.py` | Returns price and technical indicators. |
| `place_paper_order` | `app/broker/router.py` | Directly submits a paper order after kill-switch check. |
| `get_orders_list` | `app/broker/router.py` | Returns Alpaca order history. |

### Market data and indicators

| Function | File | Simple responsibility |
|---|---|---|
| `_is_rate_limited` | `app/market_data/alpaca_client.py` | Detects Alpaca HTTP 429 errors. |
| `MarketDataCollector.__init__` | `app/market_data/alpaca_client.py` | Requires server Alpaca credentials and builds SDK client. |
| `MarketDataCollector.fetch_latest_bars` | `app/market_data/alpaca_client.py` | Concurrently fetches and optionally validates watchlist bars. |
| `MarketDataCollector.fetch_latest_bars._fetch_single_symbol` | `app/market_data/alpaca_client.py` | Fetches/parses one symbol's bars. |
| `MarketDataCollector._get_bars_with_retry` | `app/market_data/alpaca_client.py` | Retries rate-limited SDK requests. |
| `create_market_data_collector` | `app/market_data/alpaca_client.py` | Builds a collector from settings. |
| `calculate_indicators` | `app/market_data/indicators.py` | Validates prices and calculates RSI-14/SMA-20/SMA-50. |
| `get_bars` | `app/market_data/router.py` | Protected API endpoint for latest OHLCV bars. |
| `MarketDataValidationError.__init__` | `app/market_data/validation.py` | Stores symbol, reason code, and readable validation error. |
| `_parse_timestamp` | `app/market_data/validation.py` | Parses an OHLCV timestamp. |
| `validate_ohlcv_bars` | `app/market_data/validation.py` | Rejects malformed, unordered, or unsafe bars. |

### Risk, controls, and policy helpers

| Function | File | Simple responsibility |
|---|---|---|
| `AllocationResult` data flow | `app/risk/allocation.py` | Holds exposure calculation results. |
| `check_maximum_allocation` | `app/risk/allocation.py` | Checks projected symbol exposure against its cap. |
| `check_sufficient_cash` | `app/risk/cash_reserve.py` | Checks order cost after cash reserve. |
| `fetch_cash_and_equity_for` | `app/risk/cash_reserve.py` | Reads cash/equity from an Alpaca client. |
| `validate_cash_reserve` | `app/risk/cash_reserve.py` | Fetches cash and fails closed on errors. |
| `execute_with_cash_check` | `app/risk/cash_reserve.py` | Submits only after the cash check passes. |
| `trading_day` | `app/risk/daily_limits.py` | Converts a timestamp to the US/Eastern trading date. |
| `_parse_timestamp` | `app/risk/daily_limits.py` | Parses and validates an order timestamp. |
| `count_orders_today` | `app/risk/daily_limits.py` | Counts all submitted orders for the current trading day. |
| `daily_pnl_pct` | `app/risk/daily_limits.py` | Calculates current equity change from previous equity. |
| `execute_with_allocation_check` | `app/risk/execution.py` | Submits only after allocation validation. |
| `get_kill_switch_control` | `app/risk/kill_switch.py` | Loads the global DB control row. |
| `is_kill_switch_enabled` | `app/risk/kill_switch.py` | Combines environment and DB kill-switch state. |
| `set_kill_switch` | `app/risk/kill_switch.py` | Creates or updates the DB kill-switch row. |
| `_parse_dt` | `app/risk/market_hours.py` | Parses optional clock timestamps. |
| `parse_clock` | `app/risk/market_hours.py` | Converts Alpaca clock JSON to `MarketClock`. |
| `fetch_market_clock` | `app/risk/market_hours.py` | Calls Alpaca's `/v2/clock` endpoint. |
| `fetch_market_clock_for` | `app/risk/market_hours.py` | Reads the clock through an existing Alpaca client. |
| `check_market_open` | `app/risk/market_hours.py` | Allows execution only while the market is open. |
| `validate_market_hours` | `app/risk/market_hours.py` | Fetches clock data and fails closed on errors. |
| `execute_with_market_hours_check` | `app/risk/market_hours.py` | Submits only when market-hours validation passes. |
| `PolicyContext.action` | `app/risk/policies.py` | Returns the proposal action. |
| `PolicyContext.symbol` | `app/risk/policies.py` | Returns the proposal symbol as text. |
| `PolicyContext.order_value` | `app/risk/policies.py` | Calculates price multiplied by quantity. |
| `_eval` | `app/risk/policies.py` | Creates a PASS/FAIL policy evaluation with evidence. |
| `_na` | `app/risk/policies.py` | Creates a not-applicable evaluation. |
| `p001_paper_only` | `app/risk/policies.py` | Enforces paper-only execution. |
| `p002_kill_switch` | `app/risk/policies.py` | Enforces the global kill switch. |
| `p003_market_open` | `app/risk/policies.py` | Enforces open market hours. |
| `p004_fresh_data` | `app/risk/policies.py` | Enforces the ten-minute data-age limit. |
| `p005_allowlist` | `app/risk/policies.py` | Enforces the watchlist symbol allowlist. |
| `p007_max_allocation` | `app/risk/policies.py` | Enforces BUY symbol exposure limit. |
| `p008_available_cash` | `app/risk/policies.py` | Enforces cash reserve for BUYs. |
| `p009_sell_ownership` | `app/risk/policies.py` | Prevents selling more shares than owned. |
| `p010_open_order_conflict` | `app/risk/policies.py` | Prevents conflicting open orders. |
| `p011_daily_loss` | `app/risk/policies.py` | Enforces the daily loss threshold. |
| `p012_max_orders_per_day` | `app/risk/policies.py` | Enforces the daily order-count threshold. |
| `_policy_token` | `app/risk/policies.py` | Creates an HMAC proof for an allowed proposal. |
| `evaluate_policies` | `app/risk/policies.py` | Runs all policies and returns ALLOW or DENY with evidence. |

---

## 14. Current limitations and behavior to remember

1. **Paper only:** The URL guard and account constraints are designed to make
   live-money Alpaca execution unavailable.
2. **No raw-bar database history:** Bars are fetched from Alpaca and summarized
   into cycle snapshots; they are not separately persisted.
3. **No dedicated decision table:** `AgentCycle.proposal_json` is the current
   decision record.
4. **No dedicated audit-event table:** `AuditEventRead` exists as a schema, but
   the ORM/persistence model is not implemented.
5. **Direct broker orders differ from agent orders:** The direct broker endpoint
   checks the kill switch but does not create a cycle-linked `BrokerOrder`.
6. **Scheduler market-clock credentials:** Scheduler clock checks use server
   `ALPACA_*` settings, while user-triggered order execution uses the user's
   encrypted account credentials.
7. **Default-agent provisioning:** A manual cycle without an agent can
   provision a demo account row with placeholder ciphertext. Real execution
   still requires valid connected credentials that can be decrypted.
8. **Database migrations:** Startup table creation is used instead of Alembic.
9. **Policy token verification:** The policy engine creates an HMAC token and
   the gateway checks that one exists, but the gateway does not yet recompute
   and verify the token's contents.
10. **Invalid LLM output:** After one repair attempt, invalid output ends the
    cycle as `FAILED` with `LLM_OUTPUT_INVALID`; it is not converted into HOLD.
11. **Inactive users:** The current JWT dependency confirms that a user exists,
    but does not currently enforce `is_active=False` as an authentication
    rejection.

---

## 15. Tests and validation

Backend tests live in [backend/tests](tests). Useful focused commands from the
repository root are:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_agent_orchestrator.py backend\tests\test_policies.py -q
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_scheduler.py -q
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_broker_gateway.py backend\tests\test_broker_reconcile.py -q
```

The complete backend quality commands are:

```powershell
cd backend
ruff check app tests
mypy
pytest
```
