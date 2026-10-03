"""Persist one canonical workflow state per editorial event, across all workers."""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE pipeline_items (
      event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
      status VARCHAR(30) NOT NULL DEFAULT 'CRAWLED',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      error_message TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0,
      CHECK (status IN ('CRAWLED','SELECTED','SCRIPT_GENERATING','SCRIPT_REVIEW',
       'SCRIPT_APPROVED','AUDIO_GENERATING','RENDERING','VIDEO_REVIEW',
       'READY_TO_PUBLISH','PUBLISHING','PUBLISHED','FAILED')),
      CHECK (retry_count >= 0)
    );
    CREATE INDEX ix_pipeline_status ON pipeline_items(status);

    CREATE FUNCTION refresh_pipeline(eid INTEGER) RETURNS VOID LANGUAGE plpgsql AS $$
    DECLARE ev events; sj script_jobs; sv script_versions; vj video_jobs; vv video_versions; pj publish_jobs;
      next_state TEXT; retries INTEGER := 0; failure TEXT := NULL;
    BEGIN
      SELECT * INTO ev FROM events WHERE id=eid FOR UPDATE;
      IF NOT FOUND THEN RETURN; END IF;
      next_state := CASE WHEN ev.decision='selected' THEN 'SELECTED' ELSE 'CRAWLED' END;
      SELECT * INTO sv FROM script_versions WHERE event_id=eid ORDER BY version DESC LIMIT 1;
      SELECT * INTO sj FROM script_jobs WHERE event_id=eid AND kind='script' ORDER BY id DESC LIMIT 1;
      IF sv.id IS NOT NULL THEN
        next_state := CASE WHEN sv.status='approved' THEN 'SCRIPT_APPROVED' ELSE 'SCRIPT_REVIEW' END;
        SELECT * INTO vv FROM video_versions WHERE script_version_id=sv.id ORDER BY version DESC LIMIT 1;
        SELECT * INTO vj FROM video_jobs WHERE script_version_id=sv.id ORDER BY id DESC LIMIT 1;
        IF vv.id IS NOT NULL THEN
          next_state := CASE WHEN vv.status='approved' THEN 'READY_TO_PUBLISH' ELSE 'VIDEO_REVIEW' END;
          SELECT * INTO pj FROM publish_jobs WHERE video_version_id=vv.id ORDER BY id DESC LIMIT 1;
          IF pj.id IS NOT NULL THEN
            retries := pj.failures;
            next_state := CASE WHEN pj.status='published' THEN 'PUBLISHED'
              WHEN pj.status IN ('failed','unknown_outcome') THEN 'FAILED'
              WHEN pj.status IN ('cancelled','stopped') THEN 'READY_TO_PUBLISH'
              ELSE 'PUBLISHING' END;
            IF next_state='FAILED' THEN failure := 'Publish job requires review'; END IF;
          END IF;
        END IF;
        IF vj.id IS NOT NULL AND (vv.id IS NULL OR vj.created_at >= vv.created_at) AND vj.status <> 'succeeded' THEN
          next_state := CASE WHEN vj.status IN ('queued','generating_audio') THEN 'AUDIO_GENERATING'
            WHEN vj.status='rendering' THEN 'RENDERING'
            WHEN vj.status='cancelled' THEN 'SCRIPT_APPROVED' ELSE 'FAILED' END;
          IF next_state='FAILED' THEN failure := 'Render job requires review'; END IF;
        END IF;
      END IF;
      IF sj.id IS NOT NULL AND (sv.id IS NULL OR sj.created_at >= sv.created_at) AND sj.status <> 'succeeded' THEN
        retries := greatest(sj.attempts-1,0);
        next_state := CASE WHEN sj.status IN ('failed','unknown_outcome') THEN 'FAILED' ELSE 'SCRIPT_GENERATING' END;
        IF next_state='FAILED' THEN failure := 'Script job requires review'; END IF;
      END IF;
      INSERT INTO pipeline_items(event_id,status,created_at,updated_at,error_message,retry_count)
        VALUES(eid,next_state,ev.created_at,clock_timestamp(),failure,retries)
      ON CONFLICT(event_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
        error_message=excluded.error_message,retry_count=excluded.retry_count;
    END $$;

    CREATE FUNCTION track_pipeline() RETURNS TRIGGER LANGUAGE plpgsql AS $$
    DECLARE eid INTEGER;
    BEGIN
      IF TG_TABLE_NAME='events' THEN eid := NEW.id;
      ELSIF TG_TABLE_NAME IN ('script_jobs','script_versions') THEN eid := NEW.event_id;
      ELSIF TG_TABLE_NAME IN ('video_jobs','video_versions') THEN
        SELECT event_id INTO eid FROM script_versions WHERE id=NEW.script_version_id;
      ELSE
        SELECT s.event_id INTO eid FROM video_versions v JOIN script_versions s ON s.id=v.script_version_id WHERE v.id=NEW.video_version_id;
      END IF;
      IF eid IS NOT NULL THEN PERFORM refresh_pipeline(eid); END IF;
      RETURN NEW;
    END $$;
    """)
    for table in ('events', 'script_jobs', 'script_versions', 'video_jobs', 'video_versions', 'publish_jobs'):
        op.execute(f'CREATE TRIGGER dashboard_pipeline AFTER INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION track_pipeline()')
    op.execute('SELECT refresh_pipeline(id) FROM events')


def downgrade():
    for table in ('events', 'script_jobs', 'script_versions', 'video_jobs', 'video_versions', 'publish_jobs'):
        op.execute(f'DROP TRIGGER dashboard_pipeline ON {table}')
    op.execute('DROP FUNCTION track_pipeline(); DROP FUNCTION refresh_pipeline(INTEGER); DROP TABLE pipeline_items')
