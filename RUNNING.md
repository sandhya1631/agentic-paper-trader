# Running the app locally

A quickstart for bringing up the full stack on a dev machine: the **Next.js
frontend**, the **FastAPI backend**, and a **PostgreSQL** database. These are three
independent services — there is no `docker-compose` wrapper yet, so you start each
one yourself.

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

## 2. PostgreSQL (via Docker)

The backend opens a DB connection on startup and **refuses to start** if Postgres
isn't reachable, so bring the database up first. This one-shot container matches
the defaults in `backend/.env.example` exactly (user/password `postgres`, database
`agentic_paper_trader`), so `CREATE DATABASE` is handled automatically on first
boot.

```powershell
docker run -d --name apt-postgres `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=agentic_paper_trader `
  -p 5432:5432 `
  postgres:16
```

Verify the daemon is reachable first with `docker info` (if it errors with
`npipe:////./pipe/dockerDesktopLinuxEngine ... cannot find the file`, Docker
Desktop isn't started yet).

Manage it later with `docker stop apt-postgres` / `docker start apt-postgres`.
Data lives inside the container unless you add a volume.

## 3. Backend (FastAPI)

In a **new** terminal:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env        # then edit DATABASE_URL / JWT_SECRET if needed
uvicorn app.main:app --reload --port 8000
```

With Postgres up you should see `Application startup complete` (not a traceback).

- Interactive API docs (Swagger): **http://localhost:8000/docs**
- Health/readiness check: **http://localhost:8000/health/db** (runs `SELECT 1`)

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
