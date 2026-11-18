# Integration tests (live services)

The default test suite (`pytest -m "not integration"`, what CI runs on every PR) uses
fakes for Alpaca and the LLM. The **integration tier** checks the same code against
real services: Alpaca paper, a live LLM and a full agent cycle on a real Postgres.

Every integration test needs **both** the `integration` marker **and** its own
environment flag. Without the flag, or without its keys, the test **skips** and says
what to set. A bare `pytest` never calls a live service, and an unconfigured run is
green.

## The tests

| File | What it checks | Flag(s) | Side effects |
|---|---|---|---|
| `backend/tests/integration/test_live_alpaca.py` | Paper account, market clock, positions, open orders, latest IEX bars | `ALPACA_INTEGRATION=1` | None (read-only) |
| `backend/tests/integration/test_live_llm.py` | A real `generate()` on the S-001 prompt returns a schema-valid `DecisionProposal` | `OLLAMA_INTEGRATION=1`, `OPENAI_INTEGRATION=1`, `ANTHROPIC_INTEGRATION=1` (one case each) | OpenAI/Claude cases are **billed** (one short call each) |
| `backend/tests/integration/test_live_cycle.py` | `run_agent_cycle` end to end: real bars, the real paper account, the configured `LLM_PROVIDER`; an `OBSERVE_ONLY` agent, so nothing is submitted | `ALPACA_INTEGRATION=1` **and** the configured provider's flag | One LLM call (billed for OpenAI/Claude) |
| `backend/tests/integration/test_live_order_roundtrip.py` | A 1-share limit BUY at half the last price through the real broker gateway, deduplicated on resubmit, then cancelled and reconciled as `CANCELED` | `ALPACA_INTEGRATION_ORDERS=1` | **Places and cancels a real paper order.** Ask the team before running it. Run it on a paper account the trading run doesn't use, or after the close: the order counts toward P-012's daily cap and, while open, P-010 blocks AAPL orders. Never enabled in CI. |
| `backend/tests/test_llm.py::test_ollama_live_health_and_generate` | Ollama health and one generation (older test) | `OLLAMA_INTEGRATION=1` | None |

Keys come from `backend/.env` or the environment through `Settings`, as the app reads
them: `ALPACA_API_KEY`/`ALPACA_API_SECRET` (paper), `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`. The tests never print them.

## Running locally

1. Start Postgres (and Ollama if you test it). See `RUNNING.md` §2, or
   `docker compose --env-file backend/.env up -d postgres` from the repo root.
   For Ollama, run `ollama pull llama3.1` on the host.
2. From `backend/`, with the venv active and `backend/.env` filled in:

```powershell
# Read-only Alpaca + local Ollama (no cost, no orders)
$env:ALPACA_INTEGRATION = "1"; $env:OLLAMA_INTEGRATION = "1"
python -m pytest tests -m integration -rs -v

# Add a paid provider (one short call per case)
$env:OPENAI_INTEGRATION = "1"
python -m pytest tests/integration/test_live_llm.py -m integration -rs -v
```

```bash
# bash equivalent
ALPACA_INTEGRATION=1 OLLAMA_INTEGRATION=1 python -m pytest tests -m integration -rs -v
```

Notes:
- The tests use the same isolated test database as the unit tier
  (`TEST_DATABASE_URL`, or `DATABASE_URL` with a `_test` suffix). **Never point it at a
  real database**: the fixtures drop and recreate every table.
- The full cycle uses whatever `LLM_PROVIDER` is configured. On a CPU-only laptop a
  local llama3.1 cycle can exceed the Ollama client's 180-second request timeout and
  fail with `httpx.ReadTimeout` (observed on 2026-11-17: 182 s). That reflects the
  machine, not the test.
- `-rs` lists each skipped test with the flag it needs.

## In CI

`.github/workflows/integration.yml` runs the tier **only on demand** (Actions → "Integration
Tests" → Run workflow) and **nightly**. It is separate from the PR checks, which stay
fast with `-m "not integration"`.

It is opt-in per service through repository **variables** (Settings → Secrets and
variables → Actions → Variables), each set to exactly `1`. With none set, the nightly
run is skipped and a manual run reports every live test as skipped (green).

| Variable | Effect | Uses secrets |
|---|---|---|
| `INTEGRATION_ALPACA=1` | Runs the read-only Alpaca tests | `ALPACA_API_KEY`, `ALPACA_API_SECRET` (already repo secrets) |
| `INTEGRATION_OPENAI=1` | Runs the OpenAI LLM case and the full cycle with `LLM_PROVIDER=openai` | `OPENAI_API_KEY` (not a repo secret as of 2026-11-17) |

There is no Ollama in CI: the runners are CPU-only, where a llama3.1 cycle already
exceeds the request timeout on a laptop (see above). The order round-trip is never
enabled in CI.
