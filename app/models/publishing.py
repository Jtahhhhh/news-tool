"""Durable TikTok consent, encrypted credentials and upload audit."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import utcnow


class TikTokAccount(Base):
    __tablename__ = 'tiktok_accounts'
    id: Mapped[int] = mapped_column(primary_key=True)
    open_id: Mapped[str] = mapped_column(String(200), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    access_cipher: Mapped[str] = mapped_column(Text, default='')
    refresh_cipher: Mapped[str] = mapped_column(Text, default='')
    access_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default='connected')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TikTokOAuthState(Base):
    __tablename__ = 'tiktok_oauth_states'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishJob(Base):
    __tablename__ = 'publish_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    video_version_id: Mapped[int] = mapped_column(ForeignKey('video_versions.id'))
    account_id: Mapped[int] = mapped_column(ForeignKey('tiktok_accounts.id'))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    video_sha256: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    publish_id: Mapped[str | None] = mapped_column(String(64))
    upload_cipher: Mapped[str | None] = mapped_column(Text)
    upload_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    init_attempts: Mapped[int] = mapped_column(Integer, default=0)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200))
    remote_status: Mapped[str | None] = mapped_column(String(40))
    post_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Deliberately disallow a second upload of the same reviewed version/account,
    # including after timeout: an idempotency key alone cannot protect duplicates.
    __table_args__ = (UniqueConstraint('video_version_id', 'account_id', name='uq_publish_video_account'),)


class PublishAttempt(Base):
    __tablename__ = 'publish_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('publish_jobs.id'), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(40), default='dispatched')
    http_status: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
