# Agentic Paper Trader

An autonomous software engineering framework connecting Large Language Models (LLMs) to financial paper trading sandbox APIs via deterministic tool calling, quantitative safety boundaries, and full audit logging.

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
