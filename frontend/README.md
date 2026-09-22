# Agentic Paper Trader — Frontend

Operator console for the autonomous LLM paper-trading agent. Built with
**Next.js 15** (App Router, TypeScript) and **Tailwind CSS v4**.

This is the UI scaffold from Story 0.1.1 ([#15](https://github.com/sandhya1631/agentic-paper-trader/issues/15)):
a navigation header, a persistent sidebar, and base theme styling. Live data,
charts, and the API client arrive in later stories.

## Prerequisites

- **Node.js 20 or newer** (developed on Node 22). Check with `node -v`.
- **npm 10+** (bundled with Node). Check with `npm -v`.

> Windows note: run the commands below from PowerShell, Git Bash, or the VS Code
> terminal. All commands are the same across platforms.

## 1. Install dependencies

From this `frontend/` directory:

```bash
npm install
```

## 2. Run the app locally (development)

```bash
npm run dev
```

Then open **http://localhost:3000**. The dev server hot-reloads on file changes.

To use a different port:

```bash
npm run dev -- -p 3001
```

## 3. Production build & run

Build an optimized production bundle and serve it:

```bash
npm run build
npm run start          # serves the build on http://localhost:3000
```

## 4. Lint

```bash
npm run lint
```

## 5. Test

The API client is covered by unit tests (Vitest):

```bash
npm test            # run once
npm run test:watch  # watch mode
```

## Environment configuration

The API client reads the backend base URL from `NEXT_PUBLIC_API_BASE_URL`
(defaults to `http://localhost:8000`). To override it, copy the example file:

```bash
cp .env.example .env.local   # then edit NEXT_PUBLIC_API_BASE_URL
```

`.env.local` is git-ignored; `.env.example` is committed as the template.

## Available scripts

| Command              | Description                                   |
| -------------------- | --------------------------------------------- |
| `npm run dev`        | Start the development server (hot reload).    |
| `npm run build`      | Create an optimized production build.         |
| `npm run start`      | Serve the production build (run build first). |
| `npm run lint`       | Run ESLint (Next.js config).                  |
| `npm run typecheck`  | Type-check with `tsc --noEmit`.               |
| `npm test`           | Run the unit test suite (Vitest).             |
| `npm run test:watch` | Run tests in watch mode.                       |

## Project structure

```
frontend/
├─ src/
│  ├─ app/
│  │  ├─ layout.tsx          # App shell: sidebar + header + <main>
│  │  ├─ globals.css         # Tailwind + design-system theme tokens
│  │  ├─ page.tsx            # Dashboard (scaffold)
│  │  ├─ positions/page.tsx  # Placeholder route
│  │  ├─ audit/page.tsx      # Placeholder route
│  │  └─ settings/page.tsx   # Placeholder route
│  ├─ components/
│  │  ├─ header.tsx          # Top navigation header
│  │  ├─ sidebar.tsx         # Persistent navigation sidebar
│  │  ├─ page-placeholder.tsx
│  │  └─ icons.tsx           # Inline SVG icon set
│  └─ lib/
│     ├─ nav.ts              # Shared navigation config
│     ├─ api/
│     │  ├─ client.ts        # Centralized fetch client + auth interceptors (#16)
│     │  ├─ config.ts        # API base URL from env
│     │  ├─ errors.ts        # ApiError
│     │  ├─ index.ts         # Public API surface (@/lib/api)
│     │  └─ client.test.ts   # Vitest: Bearer injection + 401 handling
│     └─ auth/
│        ├─ token-store.ts   # JWT token get/set/clear (localStorage-backed)
│        ├─ token-store.test.ts
│        ├─ auth-api.ts      # login / register / logout seam for #18
│        └─ auth-api.test.ts
├─ .env.example
├─ next.config.ts
├─ package.json
├─ tsconfig.json
└─ vitest.config.ts
```

## API client (Story 0.1.2, #16)

`src/lib/api` provides a centralized fetch client with auth interceptors:

- **Request interceptor** — automatically attaches `Authorization: Bearer <token>`
  when a token is present (unless `skipAuth` is set).
- **Response interceptor** — global 401 handling: clears the stored token and
  notifies a registered handler (or emits a `window` `auth:unauthorized` event).
  A 401 from a `skipAuth` request (e.g. a failed login) is treated as a
  credentials error, so it does **not** clear the session or fire the handler.

```ts
import { api, login, logout, setUnauthorizedHandler } from "@/lib/api";

// Auth seam wired to the login UI in #18:
await login({ username, password }); // POSTs /auth/login, stores the JWT
const portfolio = await api.get("/portfolio"); // Bearer token attached
await api.post("/orders", { symbol: "AAPL", qty: 1 });
logout(); // clears the token

// Wire a global redirect on 401 once the login route exists:
setUnauthorizedHandler(() => { /* router.push("/login") */ });
```

> Endpoint paths and payload shapes in `auth-api.ts` are provisional and will be
> confirmed alongside the backend auth work in #18.

## Notes

- `node_modules/`, `.next/`, and `.env*` (except `.env.example`) are git-ignored
  via the repository root `.gitignore`.
