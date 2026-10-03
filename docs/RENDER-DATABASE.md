# Render database configuration

This change only adds database configuration. It does not deploy a service,
connect to the supplied Render database, change IP rules, rotate passwords,
or replace the local database.

Local continues loading `.env`. With `APP_ENV=production` set in the process
environment, the app ignores `.env`, requires `DATABASE_URL`, and rejects DEBUG=true.
Render's automatic `RENDER=true` flag also selects production when APP_ENV is
absent; an explicit local/test APP_ENV on Render is rejected. Production rejects
loopback hosts for both application and migration connections.
Use `.env.production.example` as a reference for Render Environment variables;
the app does not automatically load that example file.

For the supplied Render database:

- Internal host: `dpg-dauim5jncjis73fpmhp0-a`
- Database: `news_postgres_xll2`
- User: `news_postgres_xll2_user`
- Direct PostgreSQL port: 5432; pool port: 6432 only when PgBouncer is enabled.
- External host: `dpg-dauim5jncjis73fpmhp0-a.ohio-postgres.render.com`.

Use the exact URL from Render's Connect menu, with the current password. The URLs
supplied without an explicit port select 5432, not 6432. No real password is stored
in this repository. URL-encode reserved characters in passwords when constructing
a URL manually; preferably copy Render's connection string.

Direct mode needs only DATABASE_URL. Pooled mode uses DATABASE_URL on port 6432
and MIGRATION_DATABASE_URL on port 5432. Both must point to the same database.
The app normalizes postgres:// and postgresql:// to postgresql+psycopg://.
SQLAlchemy uses a bounded local pool for direct connections and NullPool for
PgBouncer. Prepared statements are disabled for PgBouncer. Connections have a
timeout and pre-ping; session_scope commits on success and rolls back on error.

`python -m app.start` runs Alembic through the direct migration connection, then
SELECT 1 and an Alembic-head check before starting the server on PORT (default
8000). It emits `Database connection: OK` without printing the URL. Startup failure
uses a generic message, without rendering driver exceptions. Standalone
`alembic upgrade head` also uses MIGRATION_DATABASE_URL when supplied.

External tools should use the external hostname with TLS (`sslmode=verify-full`)
and a limited IP allowlist. Neither is changed by this config patch. Password
rotation on Render remains an account action; after rotating, update both URLs.

Other app secrets stay in their existing environment variables. SECRET_KEY and
FACEBOOK_* are not currently consumed by this app; adding names alone would not
enable authentication or Facebook integration.

## Auto-deploy audit (2026-10-03)

Source at commit 4127593 defaulted APP_ENV to local. If Render omitted both
APP_ENV and DATABASE_URL, settings fell back to localhost:5432. This is a
confirmed code path, not a confirmed diagnosis of the deployed service: the
Render dashboard requires sign-in, and service settings/logs have not been read.

Local Compose injects DB_HOST=db, DB_PORT=5432 and POSTGRES_DB, POSTGRES_USER,
POSTGRES_PASSWORD. The local .env contains the POSTGRES_* names but does not
contain DATABASE_URL or APP_ENV. DB_NAME, DB_USER, DB_PASSWORD and POSTGRES_URL
are not read by this application's database settings.

Docker starts `python -m app.start`; its first actual database connection is
`migrate()` opening the migration engine, before Uvicorn starts. The Dockerfile
does not run migrations during build. A Render command override is still unknown.
alembic.ini has no hardcoded URL; migrations/env.py shares get_settings() with
the app, using MIGRATION_DATABASE_URL when supplied.

Configure DATABASE_URL on the actual Web Service using the database's Internal
Database URL when applicable. APP_ENV=production and DEBUG=false are explicit
recommended values. No production connection string should be committed.

The patch logs masked connection diagnostics per engine role and migration/check
phase markers. Validation: 22 configuration tests passed; one integration test
skipped because no separate TEST_DATABASE_URL was supplied. No production DB
connection or redeploy success has been verified. Still needed: identify the
service, compare environment names, capture 30-50 lines preceding the original
failure, verify its start/pre-deploy commands, then follow auto-deploy through
migration success, Database connection: OK and a healthy service.

References:
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/postgresql-connection-pooling
- https://render.com/docs/blueprint-spec
