# Dashboard implementation and audit

Open `/dashboard/` after building the frontend and starting FastAPI. The React application uses Vite, TypeScript, Tailwind, a shadcn-compatible Button, React Router, TanStack Query and a cookie-aware fetch client. Queries poll every 20 seconds; jobs poll every 4 seconds. No Redux or WebSockets are required.

## Source audit

The existing application is FastAPI + Jinja, SQLAlchemy 2 and PostgreSQL, with Alembic migrations. Routes are split between web, scripts, LLM control, video/editor and publishing modules. There was CSRF protection but no application login. Background execution is durable, database-backed work claimed by separate crawl, LLM, render and publish workers.

| Workflow | Existing entities and implementation | Dashboard integration |
| --- | --- | --- |
| Crawl / select | `sources`, `articles`, `events`, `jobs`; RSS collection and event ranking | Article search, source/status/date filters, paging, event selection/rejection |
| Script | `script_jobs`, `script_versions`, source snapshots, script reviews | Generate, edit immutable versions, review, regenerate |
| Scene | JSON scenes inside `script_versions.data`, no separate scene table | Scene editing; API IDs use `version:scene` |
| Audio / render | `tts_audio`, `media_assets`, `video_jobs`, `video_versions`, video reviews | Preview, approve, rerender; existing editor provides media/timeline controls |
| Publish | `tiktok_accounts`, `publish_jobs`, `publish_attempts` | Job overview and existing consent/account workflow |

Existing TikTok support is **video inbox upload**, not automatic direct publishing. An inbox upload must not be counted as a published post. Existing checksum-bound approval and duplicate-send protection are retained.

Migration `0009` adds `pipeline_items`, one persistent canonical state per event, with timestamps, safe error summary and retry count. PostgreSQL triggers update this state for both legacy routes and workers, including backfill. Existing worker status columns remain their operational state; the dashboard never combines arbitrary client-side booleans. Editorial rejection remains a separate decision because the requested state list has no REJECTED state. States are a projection of the latest version chain; per-operation transition guards remain in the existing services.

## API

- `/api/dashboard/summary`, `/pipeline`, `/activity`, `/states`
- `/api/health`, `/api/health/services`
- `/api/articles` and `/{id}`; PATCH, select, reject, generate-script
- `/api/scripts`, `/{id}`; PATCH, regenerate, approve, reject, render
- PATCH `/api/scenes/{version:scene}`
- `/api/videos`, `/{id}`, `/{id}/render`, `/{id}/approve`
- `/api/operations/jobs`, `/{kind}/{id}`, retry/cancel
- `/api/publish`, `/api/storage`, `/api/storage/cleanup`, `/api/settings`, `/api/sources`
- `/api/auth/session`, login, logout

The existing `/api/jobs?ids=...` is preserved for the old interface. Unified job keys include type and numeric ID. Totals are calculated in PostgreSQL. The funnel counts saved versions and events separately and labels this explicitly. Today means UTC.

## Run and deploy

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
python -m app.start
```

The Dockerfile builds the frontend in a Node stage and serves it through FastAPI (deployment Option B). This preserves same-origin HttpOnly sessions and the existing editor, OAuth and media routes. `VITE_API_URL` is empty by default. For development run `pnpm dev`; its `/api` proxy targets port 8000. For a separate frontend origin, set `VITE_API_URL` and explicit comma-separated `CORS_ORIGINS`. Cookie authentication requires same-site HTTPS domains; unrelated Render subdomains may not work with same-site cookies. A standalone Static Site requires an appropriate same-origin reverse proxy or a separately designed cross-site cookie policy; this is not currently provided.

Set these **backend-only** environment variables before exposing production:

- `ADMIN_USERNAME`: username used only on initial bootstrap.
- `ADMIN_PASSWORD`: initial password, stored only as a salted scrypt hash. Remove from environment after first successful startup; subsequent startups never reset it.
- `AUTH_SECRET_KEY`: random secret of at least 32 characters; keep stable across replicas and restarts. Rotation invalidates all sessions.
- `SESSION_SECURE=true` on Render/HTTPS. Production always uses Secure cookies.

Run migrations before starting the web app (`python -m app.start`). Startup fails if the signing key is missing, or bootstrap credentials are missing on an empty database. Authentication is required in local mode too. GET/POST `/login` and POST `/logout` support the standalone login form; dashboard API aliases remain available. Anonymous pages redirect to `/login`; APIs return 401. Sessions persist for eight hours in the database, and logout revokes the session so a copied cookie cannot be replayed. Cookies use HttpOnly and SameSite=Lax; CSRF protection remains enabled. There are no registration, password recovery, roles, or user management endpoints.

Use persistent `MEDIA_ROOT` storage for all workers. Storage adapter classes support local and injected S3/B2 clients, including short-lived presigned URLs, but current render/publish workers still use their local shared media paths. **Object-storage durability across redeploys is not implemented by these adapters alone.**

## Remaining acceptance work

This change is a working incremental dashboard, not completion of all 27 requested items. Remaining work includes: connecting worker media I/O to S3/B2, standalone Static Site deployment, native React publishing/scheduling and settings forms, per-scene AI regeneration, video deletion policy, detailed subtask/traceback tracking, comprehensive service probes, and full external-provider end-to-end/production verification. Advanced analytics/calendar/realtime remain Phase 4. Some advanced actions intentionally open the existing application screens.

Health reports LLM/TTS/TikTok as `not_probed`, never a guessed success. Storage cleanup removes only regular top-level temporary files older than 24 hours and refuses while a render is active. Errors at the frontend boundary never display raw server bodies. Secret-bearing settings are omitted rather than serialized and masked afterward.

Frontend setup references: [Vite](https://vite.dev/guide/), [shadcn Vite integration](https://ui.shadcn.com/docs/installation/vite).

## Validation on 2026-10-03

- Production frontend build passed; 5 frontend API/component tests passed.
- Browser smoke covered all 8 routes, desktop and 390px mobile, without page exceptions or horizontal page overflow.
- Clean Docker/PostgreSQL regression run: 263 passed, 2 failed. Both failures reproduce at the original HEAD: the Groq video test patches the old `video_service.httpx` location, and the request-trace test captures an empty prepared-request snapshot before source enrichment. These pre-existing failures are not reported as passes.
- A separate 11-test run covering dashboard, migration and actual worker recovery passed. Windows host tests could not execute FFmpeg-dependent cases; the Docker run included FFmpeg.
- Two additional state-chain tests passed, covering crawl/selection, failure/retry, script review/approval, audio/render, video review/approval, and publishing; `inbox` remains PUBLISHING until TikTok confirms PUBLISHED.
- Compose configuration and diff whitespace checks passed. No real LLM generation, TikTok upload, production migration, or Render deployment was performed for this dashboard verification.

The local review preview runs at `http://127.0.0.1:18080/dashboard/` using `newsroom-dashboard-preview` and the disposable `newsroom-dashboard-db` container on the `newsroom-dashboard-test` network. Its database is `news_dashboard_ui_test`, separate from regression and existing application data. It contains a clearly labeled sample article and no external worker. These preview containers can be stopped when review is finished.
