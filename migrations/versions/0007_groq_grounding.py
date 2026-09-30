"""Groq credential and durable source/repair preparation (no data rewrites)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0007'
down_revision='0006'
branch_labels=depends_on=None


def upgrade():
    op.drop_constraint('llm_credentials_provider_check','llm_credentials',type_='check')
    op.create_check_constraint('llm_credentials_provider_check','llm_credentials',"provider IN ('gemini','deepseek','groq')")
    op.add_column('script_jobs',sa.Column('source_fetch_pending',sa.Boolean(),nullable=False,server_default=sa.text('false')))
    op.add_column('script_jobs',sa.Column('source_details',JSONB,nullable=False,server_default=sa.text("'{}'::jsonb")))
    op.add_column('script_jobs',sa.Column('repair_attempts',sa.Integer(),nullable=False,server_default='0'))


def downgrade():
    raise RuntimeError('Restore backup to preserve Groq credential and generation audit')
