# Agentic Paper Trader

An autonomous software engineering framework connecting Large Language Models (LLMs) to financial paper trading sandbox APIs via deterministic tool calling, quantitative safety boundaries, and full audit logging.

## Core Features
- Deterministic Technical Analysis: Computes SMA and RSI using pure Python mathematical routines.
- LLM Function Calling: Uses typed JSON schemas to propose actions (Buy, Sell, Hold).
- Safety & Risk Guardrails: Hardcoded validation rules prevent over-allocation and invalid trades.
- Full Observability: PostgreSQL audit trail and React web dashboard displaying live decisions and rationale.

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
