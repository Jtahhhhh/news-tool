from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(20), default='rss')
    adapter: Mapped[str] = mapped_column(String(80), default='generic')
    topic: Mapped[str] = mapped_column(String(100), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('interval_minutes >= 1'), CheckConstraint("kind IN ('rss', 'html')"))


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(20), default='pending')
    manual_group: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0)
    components: Mapped[dict] = mapped_column(JSONB, default=dict)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    articles: Mapped[list['Article']] = relationship(back_populates='event')
    __table_args__ = (CheckConstraint("decision IN ('pending', 'selected', 'skipped')"),)


class Article(Base):
    __tablename__ = 'articles'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey('events.id'), index=True)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default='')
    fingerprint: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(100), default='')
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[Source] = relationship()
    event: Mapped[Event | None] = relationship(back_populates='articles')
    __table_args__ = (UniqueConstraint('source_id', 'fingerprint', name='uq_source_fingerprint'),)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey('sources.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20), default='collect')
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    owner: Mapped[str | None] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[str] = mapped_column(Text, default='Đang chờ worker')
    error: Mapped[str | None] = mapped_column(Text)
    logs: Mapped[list] = mapped_column(JSONB, default=list)
    source: Mapped[Source | None] = relationship()
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'retry', 'succeeded', 'failed')"),
        CheckConstraint("kind IN ('collect', 'rank')"),
        Index('uq_active_source_job', 'source_id', unique=True,
              postgresql_where=text("kind = 'collect' AND status IN ('queued', 'running', 'retry')")),
        Index('uq_active_rank_job', 'kind', unique=True,
              postgresql_where=text("kind = 'rank' AND status IN ('queued', 'running', 'retry')")),
    )


from .scripts import ScriptJob, ScriptVersion, ScriptSourceSnapshot, ScriptReview  # noqa: E402
from .llm_control import LLMCredential, LLMQuotaState, LLMProviderHealth, LLMAttempt, LLMPolicy  # noqa: E402
