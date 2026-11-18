# Deployment & Operations runbook

How production is configured and operated, for anyone on the team who has to deploy,
check, pause or roll back the agent during the Sprint 4 trading run (#187).

- **Setting names only.** Never write a value (key, secret, URL with credentials) here,
  in an issue, or in chat.
- **"To confirm"** marks facts that can only be read in the Render, Vercel or Supabase
  dashboards. A teammate with access fills them in; nobody guesses.
- Code on `main` is the source of truth. If this page disagrees with it, fix the page.

## 1. Topology

| Part | Platform | URL | Deploys |
|---|---|---|---|
| Frontend (Next.js) | Vercel | https://agentic-paper-trader.vercel.app | Automatically on every merge to `main` |
| Backend API (FastAPI) | Render, free tier, Docker, root directory `backend`, health check path `/health` | https://agentic-paper-trader.onrender.com | Automatically on every merge to `main` |
| Database (Postgres) | Supabase | (no public URL) | Schema changes are applied by the API at startup (below) |
| Container images | GitHub Container Registry | (via `.github/workflows/cd.yml`) | The CD workflow only publishes images; it does not deploy Render |

- **Render's free tier sleeps** after about 15 minutes without inbound requests. The
  first request after that waits for a cold start (about 30–60 s).
- **No migrations (#122).** On every start the API runs `create_all` plus the
  idempotent `ensure_*` patches in `backend/app/db/schema.py` (`ADD COLUMN IF NOT
  EXISTS`, `ALTER TYPE ... ADD VALUE IF NOT EXISTS`). These only add, so the previous
  deploy still starts after a rollback. One caveat: rows already written with a newly
  added enum value (for example a new cycle status) can't be read by older code that
  doesn't know that value.

## 2. Settings inventory (names only)

### Render (backend service → Environment)

| Setting | Purpose | Production notes |
|---|---|---|
| `APP_ENV` | Environment name; `/health` returns it as `env`. Anything except `development` disables the development-only Alpaca key fallback | Must be `production` (the smoke check expects it) |
| `DATABASE_URL` | Supabase Postgres, async driver (`postgresql+asyncpg://...`) | Connection mode: see "Supabase" below |
| `JWT_SECRET` | Signs login tokens (HS256) | Changing it signs everyone out |
| `JWT_EXPIRATION_MINUTES` | Login lifetime; default 30 | To confirm whether it is overridden |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key that encrypts every user's stored Alpaca keys and their own AI provider keys (#191) | **Never change it.** A new key orphans all stored keys; every user would have to reconnect |
| `CORS_ALLOWED_ORIGINS` | JSON list of browser origins allowed to call the API | Must include the Vercel origin (it did when checked on 2026-11-10) |
| `FRONTEND_BASE_URL` | Base of the password-reset link written to the log | To confirm |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Server Alpaca **paper** keys: market data (bars) and the scheduler's market clock. Orders and policies use each user's own keys (section 3) | Missing → every cycle sees `NO_DATA` and ticks fail `market_clock_check_failed` |
| `ALPACA_PAPER_BASE_URL` | Must be the paper endpoint; the app refuses anything else (`core/paper_guard.py`) | Default is correct; leave it unset or paper |
| `LLM_PROVIDER` | `openai`, `anthropic` or `ollama` | `openai` (seen in `/llm/health` on 2026-11-10) |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI key and model. The model must support strict `json_schema` output and `temperature` | `gpt-4o-mini` (seen on 2026-11-10) |
| `LLM_SERVER_KEY_FALLBACK` | Whether a user with no AI provider key of their own runs on the server's LLM (`LLM_PROVIDER` and its key); default `true`. When `false`, such a user's cycles and replays fail with `LLM_UNAVAILABLE` until they connect their own key (section 3; #191) | New with #191; leave it unset to keep the default (decided: no user key means the server key) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Only if `LLM_PROVIDER=anthropic`. `ANTHROPIC_MODEL` is also the model for users' own Claude keys | Not used in production |
| `OLLAMA_TIMEOUT_SECONDS`, `OLLAMA_KEEP_ALIVE_MINUTES` | Server defaults for each agent's Ollama request timeout and model keep-alive (#203); an operator can override both per agent in Settings → Agent configuration | Not used in production (only with `LLM_PROVIDER=ollama`) |
| `KILL_SWITCH_ENABLED` | `true` forces the kill switch ON regardless of the UI (section 7) | Normally unset or `false` |
| `MAX_ORDERS_PER_DAY` | P-012 cap per US/Eastern day; default 10 | To confirm whether it is overridden |
| `SCHEDULER_EXECUTOR` | `inline` runs scheduled ticks inside the API process (#184); `celery` (default) needs a separate Celery worker | **Must be `inline`** in production (decision: no paid Render worker). To confirm it is set |
| `REDIS_URL` | Celery broker and tick history. With `inline` it can be set to an **empty** value: tick history then lives in memory and is lost on restart. Leaving it unset is not the same: it falls back to `redis://localhost:6379/0` | To confirm whether a Redis is attached |
| `SCHEDULER_ENABLED`, `SCHEDULER_INTERVAL_MINUTES` | The legacy global tick (default off, 15 min). Per-agent schedules from Settings → "Start schedule" don't need it | To confirm |
| `MAX_MARKET_DATA_AGE_MINUTES`, `DEFAULT_BAR_LIMIT`, `DEFAULT_TIMEFRAME_MINUTES`, `APPROVAL_EXPIRY_MINUTES` | Tunables with code defaults (15 min, 50 bars, 5 min, 1440 min) | Normally unset |

Every setting and its default is defined in `backend/app/core/config.py`;
`backend/.env.example` and `backend/BACKEND_DOCUMENTATION.md` §3 list the common ones.

### Vercel (frontend project → Settings → Environment Variables)

| Setting | Purpose | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | The Render API base URL | **Inlined at build time.** Changing it needs a redeploy, not just a save |

### Supabase

| Item | Notes |
|---|---|
| Connection mode used by `DATABASE_URL`: direct, session pooler or transaction pooler | **To confirm (#180).** It matters: the agent lock is a transaction-scoped advisory lock held on a dedicated connection for a whole cycle, and asyncpg's prepared-statement cache doesn't work with a transaction pooler unless disabled |
| SQL editor | Used for role promotion (section 3) |

### GitHub (repository → Settings → Secrets and variables → Actions)

| Name | Kind | Used by | State on 2026-11-18 |
|---|---|---|---|
| `PRODUCTION_API_BASE_URL` | Variable | `keep-alive.yml` | **Set** to the Render API URL. If it is ever removed, the keep-alive job fails and Render sleeps |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Secrets | `ci.yml` and the opt-in `integration.yml` | Present |
| `JWT_SECRET` | Secret | `ci.yml` | Present |
| `ALPACA_PAPER_BASE_URL`, `DATABASE_URL` | Secrets | No workflow uses them | Present |
| `INTEGRATION_ALPACA`, `INTEGRATION_OPENAI` | Variables | `integration.yml` (opt-in; see `docs/integration-tests.md`) | Not set |

## 3. Access

**Roles.** New accounts are `VIEWER`. Approving trades, running cycles, starting a
schedule and placing orders need `OPERATOR` (any owner can stop their own schedule); the
kill switch needs `ADMIN`. Roles are read on every request,
so after a promotion the user just refreshes the page. Promote in the Supabase **SQL
editor**:

```sql
-- check first
SELECT email, role FROM users WHERE email = 'teammate@example.com';
-- then promote (OPERATOR or ADMIN)
UPDATE users SET role = 'OPERATOR' WHERE email = 'teammate@example.com';
```

**Alpaca paper keys.** Each user connects their own paper keys in **Settings → Alpaca
paper account → Connect**. They are encrypted with `CREDENTIAL_ENCRYPTION_KEY` and never
shown again. Without a user's own keys, the LLM sees `account_data: UNAVAILABLE` and
every cycle HOLDs, and previews or orders can't run. Connect the keys **before** the
first Trigger Now: a manual run without keys creates a placeholder account that only a
later Connect overwrites.

**AI provider keys (optional, #191).** A user can connect their own OpenAI or Claude key
in **Settings → AI provider key**. It is checked with the provider, encrypted with
`CREDENTIAL_ENCRYPTION_KEY` and never shown again. Their agent's cycles and their replays
then bill that key, with the server's model for that provider (`OPENAI_MODEL` /
`ANTHROPIC_MODEL`). Without one they use the server's key while
`LLM_SERVER_KEY_FALLBACK` is on (the default). The card says which key cycles use; each
cycle's snapshot records it as `llm_key_source` (`USER` or `SERVER`), never the key.

## 4. Scheduled trading in production

- **Inline ticks.** With `SCHEDULER_EXECUTOR=inline`, the API process runs each tick.
  Ticks skip when the kill switch is on or the market is closed. Each tick runs one
  targeted cycle per watchlist symbol.
- **Keep-alive.** `.github/workflows/keep-alive.yml` pings `GET /health` every 10
  minutes from 13:00 to 21:59 UTC on weekdays, so the free instance doesn't sleep
  through market hours. A sleeping instance runs no ticks. It reads the repository
  variable `PRODUCTION_API_BASE_URL` (set; see section 2).
  - GitHub runs scheduled workflows on a best-effort basis: it can delay them or skip
    them entirely. On 2026-11-18 none ran between 13:00 and 16:00 UTC, and Render was
    asleep at 16:00 UTC (a 36 s cold start).
  - On a trading day, check Actions → "Keep production API awake" for a run about every
    10 minutes. Missing runs mean Render sleeps and no ticks run.
  - If Render still sleeps, lower the cron interval to `*/5`.
- **Restarts and orphaned cycles (#185).** A merge or an automatic restart kills any
  cycle in progress. Since #204 (merged and deployed on 2026-11-18), the API closes
  those out at startup and in the next tick's or manual run's gate:

  | Interrupted in | Becomes |
  |---|---|
  | `PENDING`…`VALIDATING` | `FAILED` with `failure_code = INTERRUPTED` (nothing was submitted) |
  | `EXECUTING` | `RECONCILING`; the gate then looks the order up at Alpaca by its `client_order_id` and never resubmits it |

  `PENDING_APPROVAL` is untouched; it expires on its own after 24 h. An approval
  returns 409 "The agent is busy running a cycle..." while the agent's cycle or sweep
  runs; retry when it finishes.

  Never edit an `EXECUTING` or `RECONCILING` cycle by hand: an order may exist at
  Alpaca. The gate reconciles it against Alpaca; check Alpaca first if it looks stuck.

## 5. Merge window during the trading run

Every merge to `main` redeploys Render and restarts the agent.

- Merge changes to the **trading path** (agent, approval, risk, broker, scheduler, LLM
  code) only **outside 9:30–16:00 ET** on trading days.
- Docs-only, CI-only and frontend-only changes don't touch the trading path, but they
  still redeploy Render (it deploys every merge to `main`), so they also restart the
  agent. Prefer the same window unless the change is urgent.
- Before merging during a run: no approval should be mid-execution, and an AUTO sweep
  should not be running (check the dashboard's scheduler activity).
- After every merge, run the smoke check (section 6).

## 6. Smoke check (after every deploy)

1. **API, read-only, no login.** From `backend/` with the venv active:

   ```powershell
   python scripts/smoke_check.py --base-url https://agentic-paper-trader.onrender.com --expect-env production
   ```

   It checks three things. It retries `/health` for up to about 5 minutes while Render
   wakes up, and exits non-zero on any failure:

   | Request | Expected |
   |---|---|
   | `GET /health` | `env` is `production` |
   | `GET /health/db` | `status: ok` |
   | `GET /llm/health?probe=true` | 200 with `reachable: true` and `model_available: true`. For OpenAI this lists models, so no tokens are billed |

   If a call fails, open the URL in a browser to read the response.
2. **UI.** Sign in on https://agentic-paper-trader.vercel.app. The dashboard loads the
   paper account balances; no "Failed to fetch", which would point to CORS or
   `NEXT_PUBLIC_API_BASE_URL`.
3. **One cycle, nothing submitted.**
   - Settings → Agent configuration: set the approval mode to `OBSERVE_ONLY` and save.
   - Settings → Trigger Now. The cycle ends `HOLD` or `COMPLETED`. Check it in the
     Audit Trail.
   - Set the mode back to what the run uses (AUTO, or REQUIRED), and say so in the
     team channel.

Record each run (date, who, result) in the trading-run notes.

## 7. Pausing and rollback

**Kill switch (stop all order submission).**
- **UI:** an ADMIN uses Settings → Kill switch → Enable, with a required reason.
  Disabling needs no reason.
- **API:** `PUT /agent/kill-switch`, ADMIN only, with a reason. Every change is logged
  to `kill_switch_events`.
- **Effect:** scheduled ticks skip, the gateway refuses every agent order, and the
  manual order ticket is refused. Cycles still run on Trigger Now; their trades are
  refused at execution.
- **Env override:** `KILL_SWITCH_ENABLED=true` on Render forces it on even if the
  database is unreachable. It takes effect after the service restarts, and the UI
  can't turn it off.

**Stop the schedule (pause the agent without the kill switch).** Settings → "Stop
schedule". Approvals and manual runs keep working.

**Roll back the backend.** In Render, open the service → Events (deploy history),
choose the last good deploy → **Rollback**. The schema patches only add, so the older
code still starts (see the enum caveat in section 1). If the problem is in the trading path, turn the kill switch on
first. Revert the bad commit on `main` with a PR, because the next merge redeploys
`main` anyway.

**Roll back the frontend.** In Vercel, open the project → Deployments, pick the last
good production deployment → **Promote to production**. The frontend has no state, so
this is instant.

## 8. Cost (OpenAI)

**Budget cap.** Keep the production OpenAI key in a dedicated OpenAI **project**, and
set a monthly budget on that project in the OpenAI dashboard (project limits/billing).
Then a bug or a tight schedule can't spend more than the cap. **To confirm by the key
owner:** which project the key belongs to, and the cap set.

**Daily estimate.** One scheduled tick runs one LLM call per watchlist symbol, plus a
second call for each cycle whose output fails validation (the repair retry).

```
ticks_per_day   = 390 / interval_minutes          # regular session: 6.5 h = 390 min
calls_per_day   ≈ ticks_per_day × symbols × (1 + repair_rate)  + manual cycles + replay samples
daily_cost      ≈ calls_per_day × (input_tokens × input_price + output_tokens × output_price)
```

- `input_tokens` per call: open a few recent cycles' `GET /agent/cycles/{id}/token-usage`
  (shown in the Audit Trail timeline) and use the estimated input tokens. It's a
  chars/4 estimate of the system prompt plus context.
- `output_tokens`: one JSON decision, typically a few hundred tokens at most.
- Prices: use the model's current per-token prices from OpenAI's pricing page on the
  day you compute this, and write that date next to the result. This page deliberately
  has no prices, because they change.
- `POST /agent/replay` costs one call per sample (up to 100 per request), and any
  logged-in user can call it (#188).

## 9. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| The first request takes about a minute | Render cold start | Wait; check the keep-alive job ran |
| "Failed to fetch" in the UI | CORS (`CORS_ALLOWED_ORIGINS`) or a wrong `NEXT_PUBLIC_API_BASE_URL` | Fix the setting; redeploy Vercel if it was the build-time URL |
| Trigger Now returns 409 | A cycle is still active or parked for approval | Approve, reject or wait. A cycle orphaned by a restart is closed out on the next run (section 4) |
| Every cycle HOLDs | No user Alpaca keys (`account_data: UNAVAILABLE`), or the market is closed | Settings → Connect; check the time |
| No scheduled cycles during market hours | The instance slept (keep-alive runs missing), `SCHEDULER_EXECUTOR` isn't `inline`, the kill switch is on, or the schedule is stopped | Section 4 (check the keep-alive runs in Actions); Settings |
| Trigger Now returns 503 with the provider's error, e.g. "OpenAI API error: RateLimitError (HTTP 429)" | LLM misconfigured, unreachable or timed out; the cycle ends `FAILED` with `failure_code = LLM_UNAVAILABLE` (#160) | `GET /llm/health?probe=true` checks only the server's key. If the user connected their own key (Settings → AI provider key), check, reconnect or disconnect that one |

Related: `docs/demo-runbook.md` (local demo), `docs/integration-tests.md` (live-service
tests), `backend/BACKEND_DOCUMENTATION.md` (internals).
