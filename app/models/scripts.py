from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import utcnow


class ScriptSourceSnapshot(Base):
    __tablename__ = 'script_source_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey('events.id'), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScriptJob(Base):
    __tablename__ = 'script_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_fetch_pending: Mapped[bool] = mapped_column(default=False, server_default=text('false'))
    source_details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    schema_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    prepared_requests: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    retry_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[int | None] = mapped_column(ForeignKey('events.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20), default='script', server_default='script')
    test_credential_id: Mapped[int | None] = mapped_column(Integer)
    routing: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    credential_id: Mapped[int | None] = mapped_column(Integer)
    route_index: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    cancelled: Mapped[bool] = mapped_column(default=False, server_default=text('false'))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    tone: Mapped[str] = mapped_column(String(20))
    target_seconds: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    output_limit: Mapped[int] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, default='')
    prompt_text: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(30), default='1.0')
    schema_version: Mapped[str] = mapped_column(String(30), default='1.0')
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey('script_source_snapshots.id'))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(64))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_kind: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    logs: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','retry_wait','waiting_quota','succeeded','failed','unknown_outcome')", name='ck_script_job_status'),
        CheckConstraint('attempts >= 0 AND attempts <= 6'),
        Index('uq_active_script_event', 'event_id', unique=True,
              postgresql_where=text("status IN ('queued','running','retry_wait','waiting_quota')")),
    )


class ScriptVersion(Base):
    __tablename__ = 'script_versions'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey('events.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(ForeignKey('script_jobs.id'), unique=True)
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey('script_versions.id'))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('script_source_snapshots.id'))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20), default='llm')
    outcome: Mapped[str] = mapped_column(String(30))
    status: Mapped[str | None] = mapped_column(String(20))
    data: Mapped[dict | None] = mapped_column(JSONB)
    raw_output: Mapped[str] = mapped_column(Text, default='')
    validation_errors: Mapped[list] = mapped_column(JSONB, default=list)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0)
    prompt_version: Mapped[str] = mapped_column(String(30))
    schema_version: Mapped[str] = mapped_column(String(30))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('event_id', 'version', name='uq_script_event_version'),
        CheckConstraint("outcome IN ('draft','insufficient_evidence','validation_error')"),
        CheckConstraint("(outcome = 'draft' AND status IN ('needs_review','approved','rejected')) OR (outcome <> 'draft' AND status IS NULL)", name='ck_script_reviewable'),
    )


class ScriptReview(Base):
    __tablename__ = 'script_reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('script_versions.id'), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("decision IN ('approved','rejected')"),)
