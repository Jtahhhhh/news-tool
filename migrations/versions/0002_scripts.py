"""Independent script queue, immutable content versions and version-specific reviews."""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE script_source_snapshots (
        id SERIAL PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id),
        payload JSONB NOT NULL, digest VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL);
    CREATE INDEX ix_script_source_snapshots_event_id ON script_source_snapshots(event_id);
    CREATE TABLE script_jobs (
        id SERIAL PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id),
        idempotency_key VARCHAR(128) NOT NULL UNIQUE, request_hash VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL CHECK(status IN ('queued','running','retry_wait','succeeded','failed')),
        provider VARCHAR(20) NOT NULL, model VARCHAR(200) NOT NULL, tone VARCHAR(20) NOT NULL,
        target_seconds INTEGER NOT NULL, timeout_seconds INTEGER NOT NULL, output_limit INTEGER NOT NULL,
        feedback TEXT NOT NULL, prompt_text TEXT NOT NULL, prompt_version VARCHAR(30) NOT NULL,
        schema_version VARCHAR(30) NOT NULL, snapshot_id INTEGER REFERENCES script_source_snapshots(id),
        attempts INTEGER NOT NULL CHECK(attempts >= 0 AND attempts <= 4), owner VARCHAR(64),
        next_attempt_at TIMESTAMPTZ NOT NULL, lease_until TIMESTAMPTZ, dispatched_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL, error_kind VARCHAR(40), error TEXT, logs JSONB NOT NULL);
    CREATE INDEX ix_script_jobs_event_id ON script_jobs(event_id);
    CREATE INDEX ix_script_jobs_status ON script_jobs(status);
    CREATE UNIQUE INDEX uq_active_script_event ON script_jobs(event_id) WHERE status IN ('queued','running','retry_wait');
    CREATE TABLE script_versions (
        id SERIAL PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id), version INTEGER NOT NULL,
        job_id INTEGER UNIQUE REFERENCES script_jobs(id), parent_version_id INTEGER REFERENCES script_versions(id),
        snapshot_id INTEGER NOT NULL REFERENCES script_source_snapshots(id), provider VARCHAR(20) NOT NULL,
        model VARCHAR(200) NOT NULL, origin VARCHAR(20) NOT NULL, outcome VARCHAR(30) NOT NULL,
        status VARCHAR(20), data JSONB, raw_output TEXT NOT NULL, validation_errors JSONB NOT NULL,
        usage JSONB NOT NULL, elapsed_seconds DOUBLE PRECISION NOT NULL, prompt_version VARCHAR(30) NOT NULL,
        schema_version VARCHAR(30) NOT NULL, prompt_hash VARCHAR(64) NOT NULL, schema_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, CONSTRAINT uq_script_event_version UNIQUE(event_id,version),
        CHECK(outcome IN ('draft','insufficient_evidence','validation_error')),
        CONSTRAINT ck_script_reviewable CHECK((outcome = 'draft' AND status IN ('needs_review','approved','rejected')) OR (outcome <> 'draft' AND status IS NULL)));
    CREATE INDEX ix_script_versions_event_id ON script_versions(event_id);
    CREATE TABLE script_reviews (
        id SERIAL PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES script_versions(id),
        decision VARCHAR(20) NOT NULL CHECK(decision IN ('approved','rejected')),
        reviewer VARCHAR(100) NOT NULL, comment TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
    CREATE INDEX ix_script_reviews_version_id ON script_reviews(version_id);
    ''')


def downgrade():
    op.execute('DROP TABLE script_reviews, script_versions, script_jobs, script_source_snapshots')
