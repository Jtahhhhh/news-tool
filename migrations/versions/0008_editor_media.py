"""Original media audit and browser proxies; existing assets remain valid."""
from alembic import op
revision='0008'
down_revision='0007'
branch_labels=depends_on=None


def upgrade():
    op.execute("ALTER TABLE media_assets ADD COLUMN probe JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE media_assets ADD COLUMN proxy_key VARCHAR(500)")
    op.execute("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT conname FROM pg_constraint WHERE conrelid='media_assets'::regclass
        AND contype='c' AND pg_get_constraintdef(oid) LIKE '%media_type%'
      LOOP EXECUTE format('ALTER TABLE media_assets DROP CONSTRAINT %I',r.conname); END LOOP;
    END $$""")
    op.execute("ALTER TABLE media_assets ADD CONSTRAINT ck_media_asset_type CHECK(media_type IN ('image','video','audio'))")


def downgrade():
    raise RuntimeError('Restore backup to retain imported audio and proxy metadata')
