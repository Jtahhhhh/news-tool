"""TikTok upload jobs and review-bound media hashes. No backfilled approvals."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006'
down_revision = '0005'
branch_labels = depends_on = None


def upgrade():
    op.add_column('video_versions', sa.Column('output_sha256', sa.String(64), nullable=True))
    op.add_column('video_reviews', sa.Column('output_sha256', sa.String(64), nullable=True))
    # The table definitions are frozen here, independently of future ORM edits.
    op.create_table('tiktok_accounts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('open_id', sa.String(200), nullable=False, unique=True),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('scopes', JSONB, nullable=False),
        sa.Column('access_cipher', sa.Text, nullable=False),
        sa.Column('refresh_cipher', sa.Text, nullable=False),
        sa.Column('access_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('tiktok_oauth_states',
        sa.Column('state_hash', sa.String(64), primary_key=True),
        sa.Column('browser_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True)))
    op.create_table('publish_jobs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('video_version_id', sa.Integer, sa.ForeignKey('video_versions.id'), nullable=False),
        sa.Column('account_id', sa.Integer, sa.ForeignKey('tiktok_accounts.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False, unique=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('video_sha256', sa.String(64), nullable=False),
        sa.Column('snapshot', JSONB, nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('publish_id', sa.String(64)),
        sa.Column('upload_cipher', sa.Text),
        sa.Column('upload_expires', sa.DateTime(timezone=True)),
        sa.Column('sent_bytes', sa.BigInteger, nullable=False),
        sa.Column('owner', sa.String(64)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('next_run_at', sa.DateTime(timezone=True)),
        sa.Column('failures', sa.Integer, nullable=False),
        sa.Column('init_attempts', sa.Integer, nullable=False),
        sa.Column('poll_count', sa.Integer, nullable=False),
        sa.Column('error', sa.String(200)),
        sa.Column('remote_status', sa.String(40)),
        sa.Column('post_ids', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('video_version_id','account_id',name='uq_publish_video_account'))
    op.create_index('ix_publish_jobs_status', 'publish_jobs', ['status'])
    op.create_table('publish_attempts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.Integer, sa.ForeignKey('publish_jobs.id'), nullable=False),
        sa.Column('operation', sa.String(30), nullable=False),
        sa.Column('outcome', sa.String(40), nullable=False),
        sa.Column('http_status', sa.Integer),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)))
    op.create_index('ix_publish_attempts_job_id', 'publish_attempts', ['job_id'])


def downgrade():
    raise RuntimeError('Restore a backup; do not discard publishing consent and attempts')
