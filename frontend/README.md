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

## Available scripts

| Command         | Description                                  |
| --------------- | -------------------------------------------- |
| `npm run dev`   | Start the development server (hot reload).   |
| `npm run build` | Create an optimized production build.        |
| `npm run start` | Serve the production build (run build first).|
| `npm run lint`  | Run ESLint (Next.js config).                 |

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
│     └─ nav.ts              # Shared navigation config
├─ next.config.ts
├─ package.json
└─ tsconfig.json
```

## Notes

- No `.env` is required for this scaffold. Environment configuration (API base
  URL, auth) is introduced with the API client in Story 0.1.2 ([#16](https://github.com/sandhya1631/agentic-paper-trader/issues/16)).
- `node_modules/` and `.next/` are git-ignored via the repository root
  `.gitignore`.
