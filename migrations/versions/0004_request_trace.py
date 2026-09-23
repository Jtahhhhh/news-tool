"""Immutable requests, attempt diagnostics and six-send retry policy."""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE script_jobs ADD COLUMN schema_snapshot JSONB NOT NULL DEFAULT '{}';
    ALTER TABLE script_jobs ADD COLUMN prepared_requests JSONB NOT NULL DEFAULT '{}';
    ALTER TABLE script_jobs ADD COLUMN retry_started_at TIMESTAMPTZ;
    DO $$ DECLARE c record; BEGIN
      FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='script_jobs'::regclass
        AND contype='c' AND pg_get_constraintdef(oid) LIKE '%attempts%' LOOP
        EXECUTE format('ALTER TABLE script_jobs DROP CONSTRAINT %I',c.conname);
      END LOOP;
    END $$;
    ALTER TABLE script_jobs ADD CONSTRAINT ck_script_attempt_limit CHECK(attempts>=0 AND attempts<=6);
    ALTER TABLE llm_attempts ADD COLUMN trace JSONB NOT NULL DEFAULT '{}';
    ALTER TABLE llm_attempts ADD COLUMN raw_output TEXT;
    ALTER TABLE llm_attempts ADD COLUMN reservation_until TIMESTAMPTZ;
    UPDATE llm_policy SET data=data || '{"max_attempts": 6,"retry_base_seconds": 15,"retry_cap_seconds": 120,"retry_window_seconds": 900,"max_concurrent": 1}'::jsonb;
    UPDATE llm_policy SET data=jsonb_set(data,'{max_output_tokens_total}','49152')
      WHERE (data->>'max_output_tokens_total')::int=32768;
    """)


def downgrade():
    raise RuntimeError('Restore a backup to preserve attempt audit history')
