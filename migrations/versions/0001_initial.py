"""Initial PostgreSQL schema (frozen, independent of application models)."""
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE sources (
      id SERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL, url TEXT NOT NULL UNIQUE,
      kind VARCHAR(20) NOT NULL, adapter VARCHAR(80) NOT NULL, topic VARCHAR(100) NOT NULL,
      enabled BOOLEAN NOT NULL, interval_minutes INTEGER NOT NULL CHECK (interval_minutes >= 1),
      next_run_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      CHECK (kind IN ('rss', 'html'))
    );
    CREATE TABLE events (
      id SERIAL PRIMARY KEY, title TEXT NOT NULL, decision VARCHAR(20) NOT NULL,
      manual_group BOOLEAN NOT NULL, score DOUBLE PRECISION NOT NULL,
      components JSONB NOT NULL, reasons JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
      CHECK (decision IN ('pending', 'selected', 'skipped'))
    );
    CREATE TABLE articles (
      id SERIAL PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
      event_id INTEGER REFERENCES events(id), canonical_url TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL, summary TEXT NOT NULL, fingerprint VARCHAR(64) NOT NULL,
      topic VARCHAR(100) NOT NULL, published_at TIMESTAMPTZ, collected_at TIMESTAMPTZ NOT NULL,
      CONSTRAINT uq_source_fingerprint UNIQUE(source_id, fingerprint)
    );
    CREATE INDEX ix_articles_source_id ON articles(source_id);
    CREATE INDEX ix_articles_event_id ON articles(event_id);
    CREATE INDEX ix_articles_published_at ON articles(published_at);
    CREATE TABLE jobs (
      id SERIAL PRIMARY KEY, source_id INTEGER REFERENCES sources(id), kind VARCHAR(20) NOT NULL,
      status VARCHAR(20) NOT NULL, attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
      owner VARCHAR(64), available_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ,
      finished_at TIMESTAMPTZ, lease_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL,
      new_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
      progress TEXT NOT NULL, error TEXT, logs JSONB NOT NULL,
      CHECK (status IN ('queued', 'running', 'retry', 'succeeded', 'failed')),
      CHECK (kind IN ('collect', 'rank'))
    );
    CREATE INDEX ix_jobs_source_id ON jobs(source_id);
    CREATE INDEX ix_jobs_status ON jobs(status);
    CREATE UNIQUE INDEX uq_active_source_job ON jobs(source_id)
      WHERE kind = 'collect' AND status IN ('queued', 'running', 'retry');
    CREATE UNIQUE INDEX uq_active_rank_job ON jobs(kind)
      WHERE kind = 'rank' AND status IN ('queued', 'running', 'retry');
    ''')


def downgrade():
    op.execute('DROP TABLE jobs; DROP TABLE articles; DROP TABLE events; DROP TABLE sources;')
