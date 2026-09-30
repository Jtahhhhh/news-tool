"""One network send per lease. Retries are durable database work, never sleeps."""
import copy
import hashlib
import json
import random
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.config import get_settings
from app.database import session_scope
from app.models import Event, Article, ScriptJob, ScriptVersion, ScriptSourceSnapshot, ScriptReview, utcnow
from app.llm.base import Generation, ProviderFailure, get_provider, redact
from app.llm.schemas import Input, Output, validate_output
from app.services import llm_control as control

PROMPT = Path(__file__).resolve().parents[1] / 'llm/prompts/script_v1.txt'
ACTIVE = ('queued', 'running', 'retry_wait', 'waiting_quota')


def digest(value):
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: int = Field(gt=0)
    provider: Literal['gemini', 'deepseek', 'groq', 'fake'] | None = None
    tone: Literal['neutral', 'conversational'] = 'neutral'
    target_seconds: int = Field(default=45, ge=15, le=90)
    idempotency_key: str = Field(min_length=8, max_length=128)
    feedback: str = Field(default='', max_length=4000)
    refresh_sources: bool = False
    base_version_id: int | None = None


def latest_version(session, event_id):
    return session.scalar(select(ScriptVersion).where(ScriptVersion.event_id == event_id)
                          .order_by(ScriptVersion.version.desc()).limit(1))


def lock_event(session, event_id):
    event = session.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(404, 'Không tìm thấy nhóm tin')
    return event


def enqueue_script(session, request: CreateRequest):
    fingerprint = digest(request.model_dump())
    # Serialize duplicate keys even when they accidentally refer to different events.
    from sqlalchemy import text
    session.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                    {'key': int(digest(request.idempotency_key)[:15], 16)})
    existing = session.scalar(select(ScriptJob).where(ScriptJob.idempotency_key == request.idempotency_key))
    if existing:
        if existing.request_hash != fingerprint:
            raise HTTPException(409, 'Idempotency key đã được dùng cho yêu cầu khác')
        return existing
    event = lock_event(session, request.event_id)
    if event.decision != 'selected':
        raise HTTPException(422, 'Chỉ tạo kịch bản cho nhóm tin đã chọn')
    active = session.scalar(select(ScriptJob).where(ScriptJob.event_id == event.id, ScriptJob.status.in_(ACTIVE)))
    if active:
        raise HTTPException(409, f'Nhóm tin đang có yêu cầu #{active.id}; hãy theo dõi yêu cầu hiện tại')
    settings = get_settings()
    provider = request.provider or control.get_policy(session)['routes'][0]['provider']
    if provider not in ('gemini', 'deepseek', 'groq') and not (provider == 'fake' and settings.llm_allow_fake):
        raise HTTPException(422, 'Nhà cung cấp chưa được bật')
    latest = latest_version(session, event.id)
    if request.base_version_id is not None and (not latest or latest.id != request.base_version_id):
        raise HTTPException(409, 'Phiên bản đã thay đổi. Tải lại trang trước khi tạo lại')
    snapshot_id = latest.snapshot_id if latest and not request.refresh_sources else None
    if not snapshot_id and not session.scalar(select(Article.id).where(Article.event_id == event.id).limit(1)):
        raise HTTPException(422, 'Nhóm tin chưa có nguồn để đối chiếu')
    routing = control.capture_routing(session, provider)
    job = ScriptJob(event_id=event.id, idempotency_key=request.idempotency_key, request_hash=fingerprint,
                    provider=provider, model=routing['routes'][0]['model'], routing=routing,
                    tone=request.tone, target_seconds=request.target_seconds,
                    timeout_seconds=settings.llm_timeout_seconds, output_limit=settings.llm_max_output_tokens,
                    feedback=request.feedback, prompt_text=PROMPT.read_text(encoding='utf-8'), snapshot_id=snapshot_id)
    session.add(job)
    session.flush()
    snapshot, data = snapshot_for_job(session, job)
    prepare_requests(job, data)
    return job


def connection_input(job):
    return Input(schema_version='1.0', story_id=f'connection-{job.id}', language='vi', target_seconds=15,
                 tone='neutral', sources=[dict(source_id='connection',url='https://example.com/connection-test',
                     title='Kiểm tra kết nối giả lập',published_at=None,
                     text='Đây là dữ liệu giả lập chỉ để kiểm tra kết nối API. Không có tin thật hoặc dữ kiện dùng để xuất bản. Hãy trả về insufficient_evidence.')])


def prepare_requests(job, data):
    """Freeze source/schema and each allowed route payload before a worker can send."""
    from app.llm.registry import provider_class
    from app.llm.base import payload_hash
    if job.prepared_requests:
        return
    job.schema_snapshot = Output.model_json_schema()
    requests = {}
    for route in job.routing['routes']:
        if route['provider'] == 'fake':
            endpoint, payload = 'mock://fake', data.model_dump(mode='json')
        else:
            cls = provider_class(route['provider'])
            adapter = cls(route['model'], timeout=job.timeout_seconds, output_limit=job.output_limit)
            adapter.schema = job.schema_snapshot
            endpoint, _, payload = adapter.build_request(data, job.prompt_text, job.feedback)
        requests[route['provider'] + '/' + route['model']] = dict(endpoint=endpoint, payload=payload,
            payload_hash=payload_hash(payload), prompt_hash=digest(job.prompt_text),
            schema_hash=digest(job.schema_snapshot), snapshot_hash=digest(data.model_dump(mode='json')),
            config_hash=digest(dict(route=route,routing=job.routing,timeout=job.timeout_seconds,output_limit=job.output_limit)))
    job.prepared_requests = requests


def snapshot_for_job(session, job):
    if job.snapshot_id:
        snapshot = session.get(ScriptSourceSnapshot, job.snapshot_id)
        payload = copy.deepcopy(snapshot.payload)
    else:
        articles = session.scalars(select(Article).where(Article.event_id == job.event_id)
                                  .order_by(Article.id).limit(5)).all()
        payload = dict(schema_version='1.0', story_id=str(job.event_id), language='vi', sources=[
            dict(source_id=f'article-{a.id}', url=a.canonical_url, title=a.title,
                 published_at=a.published_at.isoformat() if a.published_at else None,
                 text=(a.summary or '').strip()[:20000] or a.title[:20000]) for a in articles])
    payload.update(tone=job.tone, target_seconds=job.target_seconds)
    data = Input.model_validate(payload)
    payload = data.model_dump(mode='json')
    if job.snapshot_id and digest(payload) == snapshot.digest:
        return snapshot, data
    snapshot = ScriptSourceSnapshot(event_id=job.event_id, payload=payload, digest=digest(payload))
    session.add(snapshot)
    session.flush()
    job.snapshot_id = snapshot.id
    return snapshot, data


def recover_scripts(session):
    jobs = session.scalars(select(ScriptJob).where(ScriptJob.status == 'running', ScriptJob.lease_until < utcnow())
                           .with_for_update(skip_locked=True)).all()
    for job in jobs:
        interrupt_job(job, 'Worker ngừng trước khi lưu kết quả')
        control.mark_interrupted(session, job)


def interrupt_job(job, message):
    if job.dispatched_at:
        job.status, job.error_kind = 'unknown_outcome', 'unknown_outcome'
        job.error = message + '; kết quả chưa xác định, có thể đã tính phí. Cần yêu cầu mới nếu muốn thử lại.'
        job.finished_at = utcnow()
    else:
        job.status = 'retry_wait'
        job.next_attempt_at = utcnow()
        job.error = message + '; chưa gửi API, đã xếp hàng phục hồi.'
    job.owner, job.lease_until = None, None


def claim_script(session):
    job = session.scalar(select(ScriptJob).where(ScriptJob.status.in_(('queued', 'retry_wait', 'waiting_quota')),
                          ScriptJob.cancelled.is_(False),
                          ScriptJob.next_attempt_at <= utcnow()).order_by(ScriptJob.next_attempt_at, ScriptJob.id)
                         .with_for_update(skip_locked=True).limit(1))
    if not job:
        return None
    job.status, job.owner = 'running', uuid.uuid4().hex
    job.dispatched_at = None
    job.lease_until = utcnow() + timedelta(seconds=job.timeout_seconds + 30)
    return job.id, job.owner


def owned_job(session, job_id, owner):
    job = session.scalar(select(ScriptJob).where(ScriptJob.id == job_id).with_for_update())
    if not job or job.status != 'running' or job.owner != owner or job.lease_until <= utcnow():
        return None
    return job


def fail_script_owned(job_id, owner, message):
    with session_scope() as session:
        job = session.scalar(select(ScriptJob).where(ScriptJob.id == job_id).with_for_update())
        if job and job.status == 'running' and job.owner == owner:
            interrupt_job(job, message)
            control.mark_interrupted(session, job)


def add_version(session, job, generation, data):
    lock_event(session, job.event_id)
    previous = latest_version(session, job.event_id)
    errors, output = [], None
    try:
        output = validate_output(data, generation.raw).model_dump(mode='json')
    except ValueError as exc:
        errors = [redact(str(exc))]
    outcome = output['decision'] if output else 'validation_error'
    version = ScriptVersion(event_id=job.event_id, version=previous.version + 1 if previous else 1,
                            parent_version_id=previous.id if previous else None, job_id=job.id,
                            snapshot_id=job.snapshot_id, provider=job.provider, model=job.model,
                            outcome=outcome, status='needs_review' if outcome == 'draft' else None,
                            data=output, raw_output=generation.raw, validation_errors=errors,
                            usage=generation.usage, elapsed_seconds=generation.elapsed,
                            prompt_version=job.prompt_version, schema_version=job.schema_version,
                            prompt_hash=digest(job.prompt_text), schema_hash=digest(job.schema_snapshot or Output.model_json_schema()))
    session.add(version)
    job.status = 'failed' if errors else 'succeeded'
    job.error_kind = 'validation_error' if errors else ('insufficient_evidence' if outcome != 'draft' else None)
    job.error = errors[0] if errors else (output['reason'] if outcome != 'draft' else None)
    job.finished_at, job.owner, job.lease_until = utcnow(), None, None
    session.flush()
    return version


def execute_script(job_id, owner):
    try:
        with session_scope() as session:
            job = owned_job(session, job_id, owner)
            if not job:
                return
            if job.event_id:
                lock_event(session, job.event_id)
            if job.kind == 'connection_test':
                data = connection_input(job)
            else:
                snapshot, data = snapshot_for_job(session, job)
            if not job.routing:
                job.routing=control.capture_routing(session,job.provider)
            prepare_requests(job, data)  # Legacy jobs are frozen once on first execution.
            # Sufficiency is a minimum input check, never a factual certification.
            if not any(len(s.text) >= 80 and len(s.text.split()) >= 12 and s.text != s.title for s in data.sources):
                raw = dict(schema_version='1.0', story_id=data.story_id, decision='insufficient_evidence',
                           reason='Nguồn quá ngắn hoặc chỉ có tiêu đề. Bổ sung nội dung nguồn trước khi viết.',
                           title='', hook='', caption='', claims=[], scenes=[], warnings=[])
                add_version(session, job, Generation(json.dumps(raw, ensure_ascii=False)), data)
                return
            key = control.reserve_attempt(session, job)
            if key is None:
                return
            provider = get_provider(job.provider, job.model, job.timeout_seconds, job.output_limit)
            if job.provider != 'fake':
                provider.key = key
            provider.schema = job.schema_snapshot
            provider.prepared_request = job.prepared_requests.get(job.provider + '/' + job.model)
            prompt, feedback = job.prompt_text, job.feedback
        result = provider.generate_script(data, prompt, feedback)
        with session_scope() as session:
            job = owned_job(session, job_id, owner)
            if job:
                if job.event_id:
                    lock_event(session, job.event_id)
                control.finish_attempt(session, job, result=result)
                job.logs = [*job.logs, dict(attempt=job.attempts, at=utcnow().isoformat(),
                                          request_id=result.request_id, elapsed=result.elapsed, usage=result.usage, result='response')]
                if job.kind == 'connection_test':
                    try:
                        validate_output(data, result.raw)
                        job.status, job.error_kind, job.error = 'succeeded', None, None
                    except ValueError:
                        job.status, job.error_kind, job.error = 'failed', 'validation_error', 'API trả lời nhưng output không đạt validator'
                    job.finished_at, job.owner, job.lease_until = utcnow(), None, None
                else:
                    add_version(session, job, result, data)
                if job.error_kind == 'validation_error':
                    from app.models import LLMAttempt
                    attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==job.id, LLMAttempt.number==job.attempts))
                    attempt.error_kind='validation_error';attempt.error=job.error;attempt.status='failed'
    except ProviderFailure as exc:
        with session_scope() as session:
            job = owned_job(session, job_id, owner)
            if not job:
                return
            if job.event_id:
                lock_event(session, job.event_id)
            control.finish_attempt(session, job, error=exc)
            job.logs = [*job.logs, dict(attempt=job.attempts, at=utcnow().isoformat(), http_status=exc.status,
                                      request_id=exc.request_id, error=redact(str(exc)), elapsed=exc.elapsed,
                                      usage=exc.usage, unknown_outcome=exc.uncertain)]
            if exc.raw and job.snapshot_id:
                data = Input.model_validate(session.get(ScriptSourceSnapshot, job.snapshot_id).payload)
                add_version(session, job, Generation(exc.raw, exc.usage, exc.elapsed, exc.request_id), data)
                # Incomplete transport output is never reviewable, even if its body resembles valid JSON.
                version = session.scalar(select(ScriptVersion).where(ScriptVersion.job_id == job.id))
                version.outcome, version.status = 'validation_error', None
                version.validation_errors = [redact(str(exc))]
                job.status, job.error_kind, job.error = 'failed', exc.kind, redact(str(exc))
            else:
                control.schedule_failure(session, job, exc)
    except Exception:
        # Unexpected preflight failures are terminal, not an unbounded recovery loop.
        with session_scope() as session:
            job = owned_job(session, job_id, owner)
            if job:
                if job.dispatched_at:
                    interrupt_job(job, 'Lỗi nội bộ khi xử lý kịch bản')
                    control.mark_interrupted(session, job)
                else:
                    job.status, job.error_kind = 'failed', 'input_error'
                    job.error = 'Không chuẩn bị được nguồn hoặc cấu hình. Kiểm tra nhóm tin trước khi tạo yêu cầu mới.'
                    job.finished_at, job.owner, job.lease_until = utcnow(), None, None


def require_latest(session, event_id, version_id):
    lock_event(session, event_id)
    version = latest_version(session, event_id)
    if not version or version.id != version_id:
        raise HTTPException(409, 'Phiên bản đã thay đổi. Tải lại trang để tránh duyệt hoặc ghi đè bản cũ')
    if session.scalar(select(ScriptJob.id).where(ScriptJob.event_id == event_id, ScriptJob.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, 'Đang tạo phiên bản mới. Chờ job hoàn tất trước khi sửa hoặc duyệt')
    return version


def checked_output(session, version, payload):
    snapshot = session.get(ScriptSourceSnapshot, version.snapshot_id)
    try:
        return validate_output(Input.model_validate(snapshot.payload), json.dumps(payload, ensure_ascii=False)).model_dump(mode='json')
    except ValueError as exc:
        raise HTTPException(422, 'Kiểm tra cấu trúc không đạt: ' + str(exc)) from None


def save_version(session, event_id, base_version_id, payload):
    previous = require_latest(session, event_id, base_version_id)
    if previous.outcome != 'draft':
        raise HTTPException(422, 'Kết quả này không phải bản nháp có thể chỉnh sửa; hãy tạo yêu cầu mới')
    payload = copy.deepcopy(payload)
    if payload.get('scenes') and isinstance(payload['scenes'][0], dict):
        payload['scenes'][0]['narration'] = payload.get('hook', '')
    output = checked_output(session, previous, payload)
    if output['decision'] != 'draft':
        raise HTTPException(422, 'Bản chỉnh sửa phải là kịch bản nháp')
    version = ScriptVersion(event_id=event_id, version=previous.version + 1, parent_version_id=previous.id,
                            snapshot_id=previous.snapshot_id, provider=previous.provider, model=previous.model,
                            origin='edit', outcome='draft', status='needs_review', data=output,
                            raw_output=json.dumps(output, ensure_ascii=False), prompt_version=previous.prompt_version,
                            schema_version=previous.schema_version, prompt_hash=previous.prompt_hash,
                            schema_hash=previous.schema_hash)
    session.add(version)
    session.flush()
    return version


def review_version(session, event_id, version_id, decision, reviewer, comment):
    version = require_latest(session, event_id, version_id)
    if version.outcome != 'draft' or version.status != 'needs_review':
        raise HTTPException(409, 'Chỉ duyệt/từ chối bản đang chờ duyệt và hợp lệ')
    checked_output(session, version, version.data)
    version.status = decision
    review = ScriptReview(version_id=version.id, decision=decision, reviewer=reviewer, comment=comment)
    session.add(review)
    session.flush()
    return review


def renderable_version(session, event_id):
    """The future video module MUST obtain content through this gate."""
    version = latest_version(session, event_id)
    if not version or version.status != 'approved' or version.provider == 'fake' or version.outcome != 'draft':
        raise HTTPException(409, 'Chưa có phiên bản mới nhất được duyệt để dựng video')
    require_latest(session, event_id, version.id)
    checked_output(session, version, version.data)
    return version
