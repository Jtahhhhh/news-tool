"""Durable credential references, quota reservations, attempts and provider health."""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE script_jobs ALTER COLUMN event_id DROP NOT NULL;
    ALTER TABLE script_jobs ALTER COLUMN next_attempt_at DROP NOT NULL;
    ALTER TABLE script_jobs ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'script';
    ALTER TABLE script_jobs ADD COLUMN test_credential_id INTEGER;
    ALTER TABLE script_jobs ADD COLUMN routing JSONB NOT NULL DEFAULT '{}';
    ALTER TABLE script_jobs ADD COLUMN credential_id INTEGER;
    ALTER TABLE script_jobs ADD COLUMN route_index INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE script_jobs ADD COLUMN reserved_microusd INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE script_jobs ADD COLUMN reserved_output_tokens INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE script_jobs ADD COLUMN cancelled BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE script_jobs DROP CONSTRAINT script_jobs_status_check;
    ALTER TABLE script_jobs ADD CONSTRAINT ck_script_job_status CHECK(status IN ('queued','running','retry_wait','waiting_quota','succeeded','failed','unknown_outcome'));
    DROP INDEX uq_active_script_event;
    CREATE UNIQUE INDEX uq_active_script_event ON script_jobs(event_id) WHERE status IN ('queued','running','retry_wait','waiting_quota');
    UPDATE script_jobs SET status='unknown_outcome' WHERE status='failed' AND error_kind='unknown_outcome';
    CREATE TABLE llm_credentials (
      id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, provider VARCHAR(20) NOT NULL CHECK(provider IN ('gemini','deepseek')),
      project_id VARCHAR(200) NOT NULL, secret_ref VARCHAR(200) NOT NULL UNIQUE, quota_group VARCHAR(200) NOT NULL,
      allowed_models JSONB NOT NULL, priority INTEGER NOT NULL, enabled BOOLEAN NOT NULL, state VARCHAR(30) NOT NULL,
      masked_suffix VARCHAR(12) NOT NULL, last_used_at TIMESTAMPTZ, error TEXT, checked_at TIMESTAMPTZ);
    CREATE TABLE llm_quota_states (
      id SERIAL PRIMARY KEY, provider VARCHAR(20) NOT NULL, quota_group VARCHAR(200) NOT NULL, model VARCHAR(200) NOT NULL,
      configured_limit INTEGER CHECK(configured_limit IS NULL OR configured_limit>0), window_seconds INTEGER CHECK(window_seconds IS NULL OR window_seconds>0),
      window_started_at TIMESTAMPTZ NOT NULL, window_used INTEGER NOT NULL, observed_calls INTEGER NOT NULL,
      blocked BOOLEAN NOT NULL, next_attempt_at TIMESTAMPTZ, error TEXT,
      CONSTRAINT uq_llm_quota_scope UNIQUE(provider,quota_group,model));
    CREATE TABLE llm_provider_health (
      id SERIAL PRIMARY KEY, provider VARCHAR(20) NOT NULL, model VARCHAR(200) NOT NULL, failures INTEGER NOT NULL,
      state VARCHAR(20) NOT NULL, next_attempt_at TIMESTAMPTZ, probe_job_id INTEGER REFERENCES script_jobs(id),
      probe_until TIMESTAMPTZ, error TEXT, CONSTRAINT uq_llm_health_scope UNIQUE(provider,model));
    CREATE TABLE llm_attempts (
      id SERIAL PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES script_jobs(id), credential_id INTEGER REFERENCES llm_credentials(id),
      number INTEGER NOT NULL, provider VARCHAR(20) NOT NULL, model VARCHAR(200) NOT NULL, quota_group VARCHAR(200),
      status VARCHAR(30) NOT NULL, reason TEXT NOT NULL, http_status INTEGER, request_id VARCHAR(300), latency DOUBLE PRECISION,
      usage JSONB NOT NULL, error_kind VARCHAR(40), error TEXT, reserved_microusd INTEGER NOT NULL, reserved_output_tokens INTEGER NOT NULL,
      started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, CONSTRAINT uq_llm_job_attempt UNIQUE(job_id,number));
    CREATE INDEX ix_llm_attempts_job_id ON llm_attempts(job_id);
    CREATE TABLE llm_policy (id INTEGER PRIMARY KEY, data JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
    ''')


def downgrade():
    raise RuntimeError('Export new queue history before a deliberate manual rollback; automatic downgrade would discard audit records')
