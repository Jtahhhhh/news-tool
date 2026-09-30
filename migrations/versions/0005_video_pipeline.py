"""Local TTS, media and versioned video review."""
from alembic import op
revision='0005';down_revision='0004';branch_labels=depends_on=None
def upgrade():
    op.execute('''
    CREATE TABLE media_assets(id SERIAL PRIMARY KEY,filename VARCHAR(255) NOT NULL,storage_key VARCHAR(500) UNIQUE NOT NULL,media_type VARCHAR(20) NOT NULL CHECK(media_type IN ('image','video')),source VARCHAR(30) NOT NULL,author VARCHAR(200) NOT NULL,license VARCHAR(200) NOT NULL,sha256 VARCHAR(64) NOT NULL,test_only BOOLEAN NOT NULL,created_at TIMESTAMPTZ NOT NULL);
    CREATE INDEX ix_media_assets_sha256 ON media_assets(sha256);
    CREATE TABLE tts_audio(id SERIAL PRIMARY KEY,text TEXT NOT NULL,text_hash VARCHAR(64) NOT NULL,cache_key VARCHAR(64) UNIQUE NOT NULL,model VARCHAR(100) NOT NULL,model_version VARCHAR(100) NOT NULL,voice VARCHAR(100) NOT NULL,config JSONB NOT NULL,storage_key VARCHAR(500) UNIQUE NOT NULL,duration_seconds FLOAT NOT NULL,sample_rate INTEGER NOT NULL,valid BOOLEAN NOT NULL,created_at TIMESTAMPTZ NOT NULL);
    CREATE TABLE video_jobs(id SERIAL PRIMARY KEY,script_version_id INTEGER NOT NULL REFERENCES script_versions(id),idempotency_key VARCHAR(128) UNIQUE NOT NULL,request_hash VARCHAR(64) NOT NULL,status VARCHAR(30) NOT NULL CHECK(status IN ('queued','generating_audio','rendering','succeeded','failed','cancelled')),stage VARCHAR(30) NOT NULL,progress INTEGER NOT NULL CHECK(progress>=0 AND progress<=100),input_snapshot JSONB NOT NULL,owner VARCHAR(64),lease_until TIMESTAMPTZ,cancelled BOOLEAN NOT NULL,checkpoint JSONB NOT NULL,logs JSONB NOT NULL,error TEXT,created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,finished_at TIMESTAMPTZ);
    CREATE INDEX ix_video_jobs_script_version_id ON video_jobs(script_version_id);CREATE INDEX ix_video_jobs_status ON video_jobs(status);CREATE UNIQUE INDEX uq_active_video_script ON video_jobs(script_version_id) WHERE status IN ('queued','generating_audio','rendering');
    CREATE TABLE video_versions(id SERIAL PRIMARY KEY,script_version_id INTEGER NOT NULL REFERENCES script_versions(id),job_id INTEGER UNIQUE REFERENCES video_jobs(id),version INTEGER NOT NULL,parent_version_id INTEGER REFERENCES video_versions(id),status VARCHAR(20) NOT NULL CHECK(status IN ('needs_review','approved','rejected')),config JSONB NOT NULL,timeline JSONB NOT NULL,input_hash VARCHAR(64) NOT NULL,output_key VARCHAR(500) UNIQUE NOT NULL,probe JSONB NOT NULL,duration_seconds FLOAT NOT NULL,test_only BOOLEAN NOT NULL,created_at TIMESTAMPTZ NOT NULL,CONSTRAINT uq_video_script_version UNIQUE(script_version_id,version));
    CREATE INDEX ix_video_versions_script_version_id ON video_versions(script_version_id);
    CREATE TABLE video_reviews(id SERIAL PRIMARY KEY,video_version_id INTEGER NOT NULL REFERENCES video_versions(id),decision VARCHAR(20) NOT NULL CHECK(decision IN ('approved','rejected')),reviewer VARCHAR(100) NOT NULL,comment TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL);
    CREATE INDEX ix_video_reviews_video_version_id ON video_reviews(video_version_id);
    ''')
def downgrade():
    raise RuntimeError('Restore backup to preserve media review history')
