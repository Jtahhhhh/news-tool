from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import utcnow


class LLMCredential(Base):
    __tablename__ = 'llm_credentials'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[str] = mapped_column(String(200))
    secret_ref: Mapped[str] = mapped_column(String(200), unique=True)
    quota_group: Mapped[str] = mapped_column(String(200))
    allowed_models: Mapped[list] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(30), default='unchecked')
    masked_suffix: Mapped[str] = mapped_column(String(12), default='••••')
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("provider IN ('gemini','deepseek','groq')"),)


class LLMQuotaState(Base):
    __tablename__ = 'llm_quota_states'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    quota_group: Mapped[str] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(200))
    configured_limit: Mapped[int | None] = mapped_column(Integer)
    window_seconds: Mapped[int | None] = mapped_column(Integer)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    window_used: Mapped[int] = mapped_column(Integer, default=0)
    observed_calls: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('provider','quota_group','model',name='uq_llm_quota_scope'),
                     CheckConstraint('configured_limit IS NULL OR configured_limit > 0'),
                     CheckConstraint('window_seconds IS NULL OR window_seconds > 0'))


class LLMProviderHealth(Base):
    __tablename__ = 'llm_provider_health'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(20), default='closed')
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_job_id: Mapped[int | None] = mapped_column(ForeignKey('script_jobs.id'))
    probe_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('provider','model',name='uq_llm_health_scope'),)


class LLMAttempt(Base):
    __tablename__ = 'llm_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    trace: Mapped[dict] = mapped_column(JSONB, default=dict, server_default='{}')
    raw_output: Mapped[str | None] = mapped_column(Text)
    reservation_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[int] = mapped_column(ForeignKey('script_jobs.id'), index=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey('llm_credentials.id'))
    number: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    quota_group: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='dispatched')
    reason: Mapped[str] = mapped_column(Text, default='')
    http_status: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(300))
    latency: Mapped[float | None] = mapped_column(Float)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_kind: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('job_id','number',name='uq_llm_job_attempt'),)


class LLMPolicy(Base):
    __tablename__ = 'llm_policy'
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
