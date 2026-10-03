"""Single administrator and revocable sessions."""
from alembic import op
import sqlalchemy as sa
revision = '0010'
down_revision = '0009'
branch_labels = depends_on = None


def upgrade():
    op.create_table('admin_users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(254), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('id = 1', name='single_admin'))
    op.create_table('admin_sessions',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_admin_sessions_expires_at', 'admin_sessions', ['expires_at'])


def downgrade():
    op.drop_table('admin_sessions')
    op.drop_table('admin_users')
