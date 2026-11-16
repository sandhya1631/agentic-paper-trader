# Capstone demo runbook — end-to-end from the UI

This is the exact click-path to demonstrate the full workflow **entirely from the browser**,
on a per-user basis: sign in → connect account → market data → proposal → policies → approval →
paper order → audit result. No Swagger or curl required.

## 0. Bring up the stack

```powershell
# Infra (Postgres + Redis) in Docker
docker compose --env-file backend/.env up -d postgres redis
# Backend (from backend/, venv interpreter)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Frontend (from frontend/)
npm run dev        # http://localhost:3000
```

The frontend reads `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) and the backend
allows CORS from `http://localhost:3000`. Every API call carries the Bearer JWT; a 401 redirects to
`/login`.

## 1. Sign in and promote the demo user
- Go to `http://localhost:3000/register`, create a user, then log in at `/login`.
- A fresh user has role **VIEWER**: read-only. Trigger Now, Start schedule, Approve/Reject and
  agent-config edits need **OPERATOR** or **ADMIN**. Settings shows Trigger Now, Start schedule and
  the agent-config card disabled with an "only operators and admins" note; on the Approvals page the
  API refuses a VIEWER's Approve/Reject (403). There is no role UI yet, so promote the user once in SQL:

  ```powershell
  docker exec apt-postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "UPDATE users SET role='ADMIN' WHERE email='<you>';"
  ```

  Use the `POSTGRES_USER` / `POSTGRES_DB` names from `backend/.env`. `ADMIN` also unlocks the kill
  switch (step 7); use `OPERATOR` to demo without it. Roles are read on every request, so just
  refresh the page.

## 2. Connect the Alpaca paper account (Settings)
- Do this **after** promoting the user (step 1), and **before** the first Trigger Now: a cycle run
  before keys are connected creates a placeholder account.
- Open **Settings** → *Alpaca paper account* → paste your Alpaca **paper** API key + secret → **Connect account**.
- Keys are encrypted at rest and never shown again. Dashboard/Positions now show live data.

## 3. See the portfolio (Dashboard / Positions)
- **Dashboard**: equity, cash, buying power, daily P/L, agent state, recent decisions.
- **Positions**: open positions with unrealized-P/L badges and open orders.

## 4. Trigger a cycle (Settings → Trigger Now)
- Click **Trigger Now** (OPERATOR or ADMIN; see step 1). This runs one agent cycle: market data →
  indicators → LLM proposal → policies.
- The default agent runs in **REQUIRED** approval mode, so a BUY/SELL proposal is parked for review
  (a HOLD completes with no order). This is the intended, transparent demo path.

## 5. Review & approve (Approvals)
- Open **Approvals**: the pending proposal shows the policy PASS/FAIL/NA table, a live expiry
  countdown, and a comment box.
- **Approve** (optionally with a comment). The backend re-checks every policy before any order is
  sent; the result (submitted / policy-denied / broker outcome) is shown. Or **Reject**.

## 6. Audit the decision (Audit)
- Open **Audit**: the cycle appears newest-first. Expand it to see the market snapshot + indicators,
  the model's rationale, the per-policy PASS/FAIL/NA evidence, the approval, and the broker outcome.

## 7. Operator controls
- **Autonomous schedule** (Settings): start a recurring schedule (e.g. every 15 min) and stop it.
  Starting needs OPERATOR or ADMIN; any owner can stop their own schedule. Scheduled ticks are
  **skipped while the market is closed** (a safety guardrail) — demo during market hours, or show
  the skip as expected behavior. Ticks are also skipped (`owner_not_operator`) if the owner has
  since been demoted to VIEWER.
- **Kill switch** (Settings, **admin only**): visible once the user is ADMIN (step 1). Enabling it
  blocks all order dispatch server-side; approving a trade while it's on returns a blocked result.

## Known scope / deferred
- **Approval mode is REQUIRED** by default and cannot be changed from the UI — there is no
  agent-config endpoint yet. Hands-off **AUTO** mode and watchlist editing would need a new backend
  endpoint (out of Sprint 3 frontend scope).
- The kill-switch toggle sends `{enabled}` only; a server-side **reason/audit** is future work (#72).
