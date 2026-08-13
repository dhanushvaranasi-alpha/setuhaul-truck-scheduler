# setuhaul-truck-scheduler

## Running locally

The app is two processes: a Python backend (deployed as Vercel serverless
functions in `api/`) and a Next.js frontend (`web/`). Locally, nothing
serves `api/*.py` the way Vercel does, so a small dispatcher
(`scratchpad/local_api_server.py`) stands in for that at `127.0.0.1:8000`;
`web/next.config.ts` proxies `/api/*` to it in dev mode.

### Prerequisites

- `uv` (Python) and `bun` (JS) installed
- A `.env` file at the repo root with:
  - `DATABASE_URL` — direct Neon connection string (scripts, `reset_demo()`)
  - `POOLED_DATABASE_URL` — pooled Neon connection string (app queries)
  - `ALLOW_RESET` — `true` to enable `reset_demo()` / `admin/tick`
  - `OPENROUTER_API_KEY` — LLM calls in the conversation layer
  - `OPTION_TOKEN_SECRET`, `CRON_SECRET` — see `src/` / `api/_cron_common.py`

### 1. Backend — API dispatcher (port 8000)

```bash
uv sync
uv run --env-file .env scratchpad/local_api_server.py
```

### 2. Frontend — Next.js (port 3000)

```bash
cd web
bun install
bun run dev
```

### 3. Open it

- Chat: http://localhost:3000
- Dashboard: http://localhost:3000/dashboard

Use `localhost`, not `127.0.0.1` — Next.js 16's dev server only allow-lists
`localhost` for cross-origin dev asset requests by default, so `127.0.0.1`
gets a silent 403 on JS chunks.

### Resetting demo state

With `ALLOW_RESET=true`, `POST /api/admin/tick` (via the dispatcher) manually
runs all four sweeps (holds, pending confirmations, escalations, warehouse
replies) without waiting on the real 1-minute cron cadence.
