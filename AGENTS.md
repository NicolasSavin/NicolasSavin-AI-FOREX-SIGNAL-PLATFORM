# AGENTS.md

## Project rules
- User-facing language: Russian
- Backend: FastAPI
- Frontend: simple static pages served by backend
- Production host: Vercel Python (`main.py` FastAPI entry). Keep `vercel.json` working.
- Render (`render.yaml`) is legacy and must not be required to serve fxpilot.ru
- Prefer modular services over monolith files
- Do not fake unavailable market data
- Clearly label proxy metrics vs real market metrics
- Preserve working paths and existing deploy where possible
- Keep API routes stable when possible
- Update README after major changes
- On Vercel: no APScheduler, no WebSocket worker, no persistent disk — use `/api/cron/tick` and `/tmp`

## Development workflow
- Inspect current repo before editing
- Make changes in small safe steps
- Keep app runnable after each step
- Prefer explicit JSON contracts for frontend/backend communication

## UI rules
- Dark professional trading UI
- Responsive layout
- Russian labels and descriptions
- Animated signal cards
- Ticker on home page
