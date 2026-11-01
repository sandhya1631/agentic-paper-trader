# Running the app locally

A quickstart for bringing up the full stack on a dev machine: the **Next.js
frontend**, the **FastAPI backend**, PostgreSQL, Redis, a local Ollama server,
and the Celery worker. Docker Compose starts the backend infrastructure and
workers; Ollama runs on the host.

This guide records a known-good run on **Windows 11 / PowerShell**. The commands
work from Git Bash or the VS Code terminal too; only the Postgres container and
the venv-activation path are Windows-specific.

> Per-service detail lives in [`frontend/README.md`](frontend/README.md) and
> [`backend/README.md`](backend/README.md). This page is the "everything at once"
> path.

## Prerequisites

- **Node.js 20+** and npm 10+ (frontend) — check with `node -v`.
- **Python 3.11+** (backend) — check with `python --version`.
- **Docker Desktop** (easiest way to get PostgreSQL). The Docker daemon must be
  **running**, not just installed — launch Docker Desktop and wait for the tray
  icon to report "Docker Desktop is running" before any `docker` command.

## 1. Frontend (standalone — no backend required)

The UI renders on its own; live data calls fail quietly until the backend is up,
so this is the fastest way to click through the app.

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The dev server hot-reloads on save.

### Routes

| Route        | Page                          |
| ------------ | ----------------------------- |
| `/`          | Dashboard (scaffold)          |
| `/positions` | Positions (placeholder)       |
| `/audit`     | Audit trail (placeholder)     |
| `/settings`  | Settings (placeholder)        |
| `/login`     | Login                         |
| `/register`  | Register                      |

The `(app)` routes share the sidebar + header shell; `/login` and `/register`
use a separate bare `(auth)` layout.

## 2. Infrastructure (via Docker Compose)

The backend opens a DB connection on startup. Scheduled processing also requires
Redis and a Celery worker. Start the Compose stack from the repository root:

```powershell
docker compose up -d --build
docker compose ps
```

Verify the daemon is reachable first with `docker info` (if it errors with
`npipe:////./pipe/dockerDesktopLinuxEngine ... cannot find the file`, Docker
Desktop isn't started yet).

Pull the configured local model once:

```powershell
docker compose exec ollama ollama pull llama3.1
```

The Compose stack persists PostgreSQL and Redis data in `postgres_data` and
`redis_data`. Ollama uses the models already installed in your local Ollama
installation.

To run only infrastructure containers while running the API from the host:

```powershell
docker compose up -d postgres redis
```

Host processes use `localhost` URLs. Containers use Compose service names such
as `redis` and `postgres`; the containerized backend and worker reach your host
Ollama through `host.docker.internal:11434`.

## 3. Backend (FastAPI)

In a **new** terminal:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env        # then edit DATABASE_URL / JWT_SECRET if needed
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

With Postgres up you should see `Application startup complete` (not a traceback).

- Interactive API docs (Swagger): **http://localhost:8000/docs**
- Health/readiness check: **http://localhost:8000/health/db** (runs `SELECT 1`)

### Scheduler prerequisites and Swagger flow

When running the API from the host, start a Celery worker in a second terminal:

```powershell
backend\.venv\Scripts\celery.exe -A app.celery_app:celery_app worker --loglevel=info --pool=solo
```

In Swagger, use this order:

1. Register and log in.
2. Click **Authorize** with `Bearer <access_token>`.
3. Connect an Alpaca paper account or configure the development fallback.
4. Run `POST /agent/cycles/run` once and copy the returned `agent_id`.
5. Run `POST /agent/scheduler/start` with:
   `{"agent_id":"<agent-id>","interval_minutes":1}`.
6. Wait one minute; the first scheduled run is interval-based, not immediate.
7. Check `GET /agent/scheduler/status` and
   `GET /agent/scheduler/logs?schedule_id=<schedule_id>`.
8. Stop it with `POST /agent/scheduler/{schedule_id}/stop`.

The schedule endpoint only registers APScheduler. APScheduler uses Redis to
deduplicate and enqueue the Celery task; the worker then loads the schedule
from PostgreSQL and runs the cycle. All three layers must be available.

If the API log shows
`redis.exceptions.TimeoutError: Timeout connecting to server`, Redis is not
reachable at `REDIS_URL`. Use `redis://localhost:6379/0` for a host API or
`redis://redis:6379/0` inside Compose, then restart the API if its environment
changed.

### Endpoints

| Method + path         | Purpose                                                   |
| --------------------- | -------------------------------------------------------- |
| `GET /health`         | Liveness (no DB)                                         |
| `GET /health/db`      | Readiness (`SELECT 1` through the async session)         |
| `POST /auth/register` | Create a user (bcrypt-hashed password)                  |
| `POST /auth/login`    | Returns a signed JWT (`access_token`)                    |
| `GET /auth/me`        | Protected; needs `Authorization: Bearer <token>` or 401 |

## How the two connect

The frontend reads `NEXT_PUBLIC_API_BASE_URL` (defaults to
`http://localhost:8000`). To override, copy `frontend/.env.example` to
`frontend/.env.local`. The API client auto-injects the Bearer JWT and handles
401s globally, so once auth is up the flow is: **register → login (stores the
token) → protected calls carry the token automatically.**

## Quick checks

```powershell
cd frontend; npm test          # Vitest
cd frontend; npm run lint      # ESLint
cd frontend; npm run typecheck # tsc --noEmit
cd backend;  pytest            # backend tests (venv activated)
```

## Troubleshooting

- **`ModuleNotFoundError: No module named 'apscheduler'` while importing `app.agent.scheduler`**
  — The traceback shows Uvicorn running from a Python environment that does not have the
  backend dependencies. The PowerShell `( .venv )` prompt and current directory don't prove
  that the `uvicorn` command resolves to the backend environment: a different `uvicorn.exe`
  earlier on `PATH` can still launch the wrong interpreter. Stop the reloader with Ctrl+C,
  then, from the `backend` directory, install and launch through the backend interpreter:

  ```powershell
  .venv\Scripts\python.exe -m pip install -e ".[dev]"
  .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
  ```

  The scheduler dependency is declared in `backend/pyproject.toml`. Installing it in a
  different environment (for example, the repository-root `.venv`) will not make it available
  to the backend `.venv`, or vice versa. To confirm which interpreter a bare command will use,
  run `Get-Command python,uvicorn | Select-Object Name,Source`; the explicit module command
  above bypasses an incorrectly resolved `uvicorn.exe`.
- **Backend traceback ending in `[Errno 10061] Connect call failed ('127.0.0.1', 5432)`**
  — nothing is listening on port 5432. PostgreSQL isn't running. This is a config
  issue, not a code bug: start the container in step 2, then re-run uvicorn.
- **`docker run` fails with `npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified`**
  — the Docker CLI is installed but the daemon is off. Start Docker Desktop, wait
  for it to report "running", confirm with `docker info`, then retry.
- **venv built from Anaconda** — if you see
  `Unable to copy ... venvlauncher.exe` during `python -m venv`, the venv may fall
  back to the Anaconda base interpreter instead of an isolated 3.11 env. It still
  runs, but for clean isolation create the venv from a standalone Python 3.11+.
- **First `docker run` is slow** — it pulls the `postgres:16` image once; later
  starts are instant.
