from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import utcnow


class MediaAsset(Base):
    __tablename__='media_assets'
    id:Mapped[int]=mapped_column(primary_key=True)
    filename:Mapped[str]=mapped_column(String(255))
    storage_key:Mapped[str]=mapped_column(String(500),unique=True)
    media_type:Mapped[str]=mapped_column(String(20))
    source:Mapped[str]=mapped_column(String(30),default='upload')
    author:Mapped[str]=mapped_column(String(200),default='')
    license:Mapped[str]=mapped_column(String(200),default='user-provided')
    sha256:Mapped[str]=mapped_column(String(64),index=True)
    test_only:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint("media_type IN ('image','video')"),)


class TTSAudio(Base):
    __tablename__='tts_audio'
    id:Mapped[int]=mapped_column(primary_key=True)
    text:Mapped[str]=mapped_column(Text)
    text_hash:Mapped[str]=mapped_column(String(64))
    cache_key:Mapped[str]=mapped_column(String(64),unique=True)
    model:Mapped[str]=mapped_column(String(100))
    model_version:Mapped[str]=mapped_column(String(100))
    voice:Mapped[str]=mapped_column(String(100))
    config:Mapped[dict]=mapped_column(JSONB,default=dict)
    storage_key:Mapped[str]=mapped_column(String(500),unique=True)
    duration_seconds:Mapped[float]=mapped_column(Float)
    sample_rate:Mapped[int]=mapped_column(Integer)
    valid:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class VideoJob(Base):
    __tablename__='video_jobs'
    id:Mapped[int]=mapped_column(primary_key=True)
    script_version_id:Mapped[int]=mapped_column(ForeignKey('script_versions.id'),index=True)
    idempotency_key:Mapped[str]=mapped_column(String(128),unique=True)
    request_hash:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(30),default='queued',index=True)
    stage:Mapped[str]=mapped_column(String(30),default='queued')
    progress:Mapped[int]=mapped_column(Integer,default=0)
    input_snapshot:Mapped[dict]=mapped_column(JSONB)
    owner:Mapped[str|None]=mapped_column(String(64))
    lease_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    cancelled:Mapped[bool]=mapped_column(Boolean,default=False)
    checkpoint:Mapped[dict]=mapped_column(JSONB,default=dict)
    logs:Mapped[list]=mapped_column(JSONB,default=list)
    error:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    __table_args__=(CheckConstraint("status IN ('queued','generating_audio','rendering','succeeded','failed','cancelled')"),
                    CheckConstraint('progress>=0 AND progress<=100'),
                    Index('uq_active_video_script','script_version_id',unique=True,
                          postgresql_where=text("status IN ('queued','generating_audio','rendering')")))


class VideoVersion(Base):
    __tablename__='video_versions'
    id:Mapped[int]=mapped_column(primary_key=True)
    script_version_id:Mapped[int]=mapped_column(ForeignKey('script_versions.id'),index=True)
    job_id:Mapped[int|None]=mapped_column(ForeignKey('video_jobs.id'),unique=True)
    version:Mapped[int]=mapped_column(Integer)
    parent_version_id:Mapped[int|None]=mapped_column(ForeignKey('video_versions.id'))
    status:Mapped[str]=mapped_column(String(20),default='needs_review')
    config:Mapped[dict]=mapped_column(JSONB)
    timeline:Mapped[dict]=mapped_column(JSONB)
    input_hash:Mapped[str]=mapped_column(String(64))
    output_key:Mapped[str]=mapped_column(String(500),unique=True)
    output_sha256:Mapped[str|None]=mapped_column(String(64))
    probe:Mapped[dict]=mapped_column(JSONB,default=dict)
    duration_seconds:Mapped[float]=mapped_column(Float)
    test_only:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('script_version_id','version',name='uq_video_script_version'),
                    CheckConstraint("status IN ('needs_review','approved','rejected')"),)


class VideoReview(Base):
    __tablename__='video_reviews'
    id:Mapped[int]=mapped_column(primary_key=True)
    video_version_id:Mapped[int]=mapped_column(ForeignKey('video_versions.id'),index=True)
    decision:Mapped[str]=mapped_column(String(20))
    output_sha256:Mapped[str|None]=mapped_column(String(64))
    reviewer:Mapped[str]=mapped_column(String(100))
    comment:Mapped[str]=mapped_column(Text,default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint("decision IN ('approved','rejected')"),)
