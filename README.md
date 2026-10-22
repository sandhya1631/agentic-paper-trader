# Agentic Paper Trader

An autonomous software engineering framework connecting Large Language Models (LLMs) to financial paper trading sandbox APIs via deterministic tool calling, quantitative safety boundaries, and full audit logging.

> **Running it locally?** See [RUNNING.md](RUNNING.md) for a tested, full-stack quickstart (frontend + backend + PostgreSQL).

## The Problem
The Human Retail Problem	The AI Problem
Many retail day-traders lose money due to emotional trading and lack of systematic risk management.	LLMs are generative and probabilistic: they hallucinate numbers, miscalculate portfolio math, and suffer catastrophic errors if allowed to trade unchecked.
Real-time market data is overwhelming; retail investors lack the tools to track indicators, news, and positions simultaneously.	No existing consumer tool bridges natural-language reasoning with zero-trust safety checks.

## The Vision
Democratized, Autonomous, & Transparent Wealth Intelligence
Long-Term Vision: A fully self-driving, personal hedge fund engine for everyday individuals.
Multimodal: An autonomous agent that reads earnings call transcripts, and live price action simultaneously.  
Explainable AI : Every single transaction is fully transparent giving everyday people the institutional-grade risk discipline.

## Architecture: Three Layers

Layer 1 — Context
Real 5-minute price bars
Pre-calculated technical indicators (14-period RSI, 20-period SMA) using Python math libraries

Layer 2 — Probabilistic Reasoning (The Agent)
Evaluates synthesized market context against strategy instructions
Emits structured JSON tool calls (place_order, hold)

Layer 3 — Deterministic Execution & Safety Boundary
Code-level validation: rejects any order that violates capital caps (max 10% of portfolio) or trading hours
Dispatches approved orders to the Trading API

## Core Features
- Deterministic Technical Analysis: Computes SMA and RSI using pure Python mathematical routines.
- LLM Function Calling: Uses typed JSON schemas to propose actions (Buy, Sell, Hold).
- Safety & Risk Guardrails: Hardcoded validation rules prevent over-allocation and invalid trades.
- Full Observability: PostgreSQL audit trail and React web dashboard displaying live decisions and rationale.

## Versioning and testing

Strategy versions identify immutable system prompts. Each agent config selects a registered ID
(currently `S-001`); the ORM rejects unknown IDs when a config is created or updated, and the
cycle runner resolves that ID before invoking the LLM. To change strategy behavior, add a new
entry (for example, `S-002`) in `backend/app/agent/prompt.py` and explicitly move agents to it.
Do not edit an existing version's prompt in place: completed cycles retain the prompt and
snapshot used to produce their decisions.

Each cycle's saved `snapshot_json` includes the selected strategy version and the current
deterministic risk-policy version (`POLICY_VERSION`). These are distinct: changing the prompt
does not change risk rules. The policy version is a ruleset stamp, not evidence that the policy
evaluator was run for that cycle; the current cycle runner does not invoke policy evaluation.
`AgentConfig.version` is not the strategy version and is not currently used for optimistic
concurrency.

Run focused tests from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_agent_orchestrator.py backend\tests\test_policies.py -q
```

The orchestrator tests verify prompt lookup, rejection of unknown strategy IDs, propagation of
the chosen prompt to the LLM, and persistence of strategy/policy version metadata. The policy
tests exercise deterministic safety rules independently of the LLM. Orchestrator integration
tests use the isolated PostgreSQL test database configured by `TEST_DATABASE_URL` (or derived
from `DATABASE_URL` with a `_test` suffix); never point it at production. Database migrations
are not configured yet, so deployed schema changes still require a separate migration setup.

## Scheduler architecture and testing

There are two ways to schedule cycles:

- **Global interval:** `SCHEDULER_ENABLED=true` starts a global job at
  `SCHEDULER_INTERVAL_MINUTES` (default: 15). Each tick processes enabled agents that do not
  have an individual schedule.
- **Per-agent interval:** the authenticated `POST /agent/scheduler/start` endpoint accepts
  `{"agent_id":"<uuid>","interval_minutes":5}`. It creates/updates a recurring schedule for
  that user's enabled agent. The first run is after the requested interval, not immediately.
  Schedules are stored in PostgreSQL and restored at app startup. Stop one with
  `POST /agent/scheduler/{schedule_id}/stop`.

Both jobs check the kill switch and Alpaca market clock before running a cycle. Poll the
authenticated `GET /agent/scheduler/status` endpoint to see active schedules, next run times,
the latest shared tick summary, and recent scheduled cycles. Tick summaries are shared through
Redis; cycle, approval, and broker-order records remain in PostgreSQL.
Use authenticated `GET /agent/scheduler/logs` to inspect up to 50 recent interval results
(including `SKIPPED` results such as `market_closed`); pass `schedule_id` to filter to one
schedule or `limit` to choose 1–100 entries. Redis retains the last 100 tick results per
scheduler/schedule for up to 30 days.

The global kill switch is available at `GET /agent/kill-switch` and `PUT /agent/kill-switch`
with `{"enabled":true}` or `{"enabled":false}`. Both endpoints require the `ADMIN` role.
Its API-managed state is stored in PostgreSQL and applies to all scheduled cycles and trade
policy checks. The execution gateway and direct `POST /api/v1/broker/orders` endpoint both
enforce the switch immediately before order submission.
`KILL_SWITCH_ENABLED=true` in the environment remains a force-on override for everyone and
cannot be disabled through the API. Scheduler tick history is stored in Redis, while cycle and
trade history remains in PostgreSQL.

Scheduled cycles respect the agent's approval mode:

- `OBSERVE_ONLY` saves proposals without trading.
- `REQUIRED` saves BUY/SELL proposals as pending approvals; no order is submitted until
  a user approves. Approving re-checks current account, market, and deterministic risk
  policies before any paper order is submitted.
- `AUTO` runs current account, market, and deterministic risk-policy checks and submits
  an order to Alpaca paper only if policies allow it.

Manual cycles follow the same approval-mode rules.
Before a new cycle starts, any cycle left in `RECONCILING` is checked against Alpaca using its
persisted client order ID. The agent stays blocked if the broker outcome is still unknown.

Approval endpoints for pending proposals:

- `GET /agent/approvals/pending` lists pending proposals.
- `POST /agent/approvals/{approval_id}/approve` records approval and re-checks current
  policies before attempting execution; `POST /agent/approvals/{approval_id}/reject` rejects it.

Automatic submission is restricted to a connected paper account and the official Alpaca
paper API endpoint; a policy failure blocks submission. `GET /agent/cycles/{cycle_id}` and
the scheduler status response show cycle state and any broker order/fill state. `.env`
Alpaca keys are used for scheduler market-hours checks; order execution uses the user's
connected, encrypted paper-account credentials.

Run one-off cycles immediately with `POST /agent/cycles/run`; that is separate from starting
a recurring schedule. Scheduled ticks are dispatched through Celery to workers using Redis.
When running multiple API instances, Redis-based interval deduplication prevents each instance
from enqueueing the same scheduled tick. Cycle, approval, and broker-order outcomes remain
persisted in PostgreSQL.

Run the scheduler tests from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_scheduler.py -q
```

These are isolated unit tests: database sessions, market clock responses, and cycle execution
are mocked. They verify enabled-agent selection, scheduled trigger values, closed-market and
kill-switch skips, agent-level failure isolation, interval creation, and start/stop/status API
behavior. Paper-order policy tests mock Alpaca and verify that denied proposals do not submit.
They do not make live broker calls. To smoke-test, start a per-agent schedule from `/docs`,
poll `/agent/scheduler/status`, then inspect the cycle and approval endpoints. Ensure database
access and valid Alpaca market-data credentials are configured. Keep the interval at least
1 minute; one backend worker/instance is required.

## Tech Stack
- Language: Python 3.11+
- API: FastAPI
- Broker Integration: alpaca-py (Alpaca Paper Trading API)
- Market Data / Math: Pandas
- LLM / Agent Reasoning: Cloud-hosted LLM API (structured JSON tool calling), with local Ollama fallback planned as a stretch goal
- Database: PostgreSQL / SQLAlchemy
- Frontend: React, Recharts
- Testing: PyTest (safety guardrail verification)
- DevOps: Docker & Docker Compose
- CI/CD: GitHub Actions

## Deployment

| Part | Platform | URL |
|---|---|---|
| Backend (FastAPI) | Render | https://agentic-paper-trader.onrender.com |
| Frontend (Next.js) | Vercel | https://agentic-paper-trader.vercel.app |
| Database (Postgres) | Supabase | n/a |

- API docs: [/docs](https://agentic-paper-trader.onrender.com/docs)
- Health check: [/health/db](https://agentic-paper-trader.onrender.com/health/db)

> The backend runs on Render's free tier, so the first request after inactivity can take ~50 seconds to wake up.

### Backend on Render
- Runtime: Docker
- Root directory: `backend`
- Health check path: `/health`

Environment variables (set in Render → Environment; **never commit values**):

### Frontend on Vercel
- Root directory: `frontend`
- Env var: `NEXT_PUBLIC_API_BASE_URL=https://agentic-paper-trader.onrender.com`
