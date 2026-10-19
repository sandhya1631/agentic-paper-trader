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
