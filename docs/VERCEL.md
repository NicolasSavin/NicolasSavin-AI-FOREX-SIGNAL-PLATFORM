# FXPilot on Vercel

Production moved from Render (free plan suspended) to Vercel Python Functions.

## What Vercel can and cannot do

Works:

- FastAPI site, pages, APIs (`app/main.py` via root `main.py`)
- Seeded JSON from `data/` copied to `/tmp/fxpilot-data` on cold start
- Hourly cron: `GET /api/cron/tick`

Does not persist across instances:

- Writes to disk (ideas rebuilds, media import, paper trading)
- Background APScheduler / Twelve Data WebSocket
- OrderFlow engine (still a separate service; disabled by default here)

For durable state later: Neon Postgres or Vercel Blob. Until then each cold start reseeds from the repo `data/` folder.

## Deploy

1. Import `NicolasSavin/NicolasSavin-AI-FOREX-SIGNAL-PLATFORM` in Vercel (Framework Preset: Other, entry `main.py`).
2. Set env vars (Production + Preview):

```
FXPILOT_EXECUTION_MODE=DRY_RUN
FXPILOT_SCHEDULER_ENABLED=0
TWELVEDATA_WS_ENABLED=false
ORDERFLOW_ENABLED=false
ORDERFLOW_ENGINE_ENABLED=false
CRON_SECRET=<random>
FXPILOT_OPS_TOKEN=<same as before, if any>
OPENROUTER_API_KEY=
TWELVEDATA_API_KEY=
YOUTUBE_API_KEY=
```

3. Custom domain: Project → Settings → Domains → add `fxpilot.ru` and `www.fxpilot.ru`.
4. At the registrar, replace Render DNS:

| Host | Type | Value |
|---|---|---|
| `@` | A | `76.76.21.21` |
| `www` | CNAME | `cname.vercel-dns.com` |

Remove the Render / Cloudflare records that currently point `fxpilot.ru` at `216.24.57.1`.

5. After DNS: `https://fxpilot.ru/health` must return `{"status":"ok"}`.

Hobby cron is hourly at most. GitHub Action `.github/workflows/vercel-cron.yml` can ping `/api/cron/tick` more often if `CRON_SECRET` is in repo secrets.

## Local check

```powershell
$env:VERCEL = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8010
```
