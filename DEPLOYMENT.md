# Deployment Guide

This project is suitable for a split deployment:

- Backend: Render Web Service running FastAPI.
- Frontend: MilesWeb shared hosting as static files under `public_html` or a subdirectory.

## Backend on Render

Create a new Render Web Service from the repository.

Recommended settings:

- Root directory: `backend`
- Runtime: Python, pinned by `backend/runtime.txt` to Python 3.11
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

You may also use the repository `render.yaml` blueprint.

### Render Environment Variables

| Key | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Use Render PostgreSQL, Supabase, Aiven, or another managed PostgreSQL URL. SQLite is not recommended on Render because the filesystem is ephemeral. |
| `CORS_ALLOW_ORIGINS` | Yes | Your MilesWeb frontend origin, for example `https://sathishkannan.com` or `https://www.sathishkannan.com`. Use comma-separated origins if needed. |
| `AUTH_SECRET_KEY` | Yes | Random secret used to sign lightweight bearer tokens. Generate a long unique value for Render. |
| `OPENROUTER_API_KEY` | Optional | Enables live AI briefing. Without it, the app shows a graceful AI-unavailable message. |
| `NEWS_API_KEY` | Optional | Enables fallback NewsData.io ingestion. RSS still works without it. |
| `AI_MODEL_NAME` | Optional | Defaults to `google/gemma-3-27b-it:free`. |

After deploy, verify:

- `https://your-render-service.onrender.com/`
- `https://your-render-service.onrender.com/docs`
- `https://your-render-service.onrender.com/reference/stocks`

## Frontend on MilesWeb PHP-MySQL Hosting

The frontend does not require PHP or MySQL. It is static HTML, CSS, JavaScript, and JSON data.

Upload the contents of `frontend/` to the target web root, usually one of:

- `public_html/`
- `public_html/finreview/`
- another subdomain document root

Before uploading, set the backend URL through a runtime config file instead of hardcoding it in Git:

1. Copy `frontend/config.runtime.example.php` to `frontend/config.runtime.php`.
2. Set `FINREVIEW_API_URL` in MilesWeb/cPanel/server environment to your Render backend origin, for example `https://your-render-service.onrender.com`.
3. Add this line before `config.js` in the hosted `index.html`:

```html
<script src="config.runtime.php"></script>
```

`frontend/config.runtime.php` is ignored by Git. `frontend/config.js` will use the runtime value when present and fall back to `http://localhost:8000` for local development.

The backend URL is still visible in browser developer tools because the browser must know where to send API requests. Treat API keys, database URLs, and secrets as private; treat the public API origin as configuration, not a secret.

## No Separate Staging Required

For a portfolio project, a separate staging stack is optional. A practical release flow is enough:

1. Deploy backend to Render.
2. Confirm `/docs` opens.
3. Upload frontend to MilesWeb.
4. Register a fresh account.
5. Load sample portfolio.
6. Verify dashboard, portfolio, analytics, alerts, AI briefing, and CSV import.

## Production Notes

- Keep `.env` and database URLs out of GitHub.
- Use PostgreSQL for Render.
- Set `CORS_ALLOW_ORIGINS` to the exact MilesWeb domain.
- Render free instances may sleep, so first API request can be slow.
- If using a custom domain for Render, update both `FINREVIEW_API_URL` and CORS.

## Transaction safety update

`AUTH_SECRET_KEY` is mandatory for local and hosted startup. Use a randomly
generated secret of at least 32 bytes; known example/default values are rejected.
Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`
and store it in the backend environment. Rotating it signs out existing sessions.

Apply the transaction guardrails SQL in `supabase/migrations/` before deploying
the updated API to an existing PostgreSQL database. It adds validated checks
and one holding per `(user_id, symbol)`; it does not rewrite financial data.
The update was applied to the connected finreview-api project. New local SQLite
databases receive matching basic checks from SQLModel; existing SQLite databases
should be backed up and migrated separately.

Imports are atomic for the trade ledger and holdings. Optional derived analytics
and alerts refresh after commit; `analytics_updated: false` means retry the
analytics refresh, not the trade. The existing same-day CSV hash deduplication
is preserved; two otherwise identical trades on the same day still collapse.
A future explicit broker transaction ID/import idempotency design should replace it.

MF overlap now returns `overlap_score: null` until verified underlying holdings
are connected. The frontend displays Unavailable rather than a generated score.
