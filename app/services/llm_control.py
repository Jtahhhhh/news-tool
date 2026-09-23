"""All reservations and circuit transitions are short PostgreSQL transactions.

The advisory lock serializes control-plane updates across workers. No HTTP is
performed under that lock. A reservation is conservative and never refunded
after a possible send, including crashes and timeouts.
"""
import copy
import random
from datetime import timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from app.config import get_settings
from app.models import (LLMCredential, LLMQuotaState, LLMProviderHealth, LLMAttempt, LLMPolicy, ScriptJob, utcnow)
from app.llm.secrets import resolve, mask, redact_secrets


class Route(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['gemini', 'deepseek']
    model: str = Field(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9._-]+$')
    reservation_microusd: int = Field(default=0, ge=0, le=100000000)


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    routes: list[Route] = Field(min_length=1, max_length=2)
    allowed_providers: list[Literal['gemini', 'deepseek']] = Field(default_factory=lambda:['gemini','deepseek'])
    fallback_enabled: bool = False
    fallback_on: list[Literal['service_error','quota']] = Field(default_factory=lambda:['service_error','quota'])
    max_attempts: int = Field(default=6, ge=1, le=6)
    retry_base_seconds: int = Field(default=15, ge=1, le=120)
    retry_cap_seconds: int = Field(default=120, ge=1, le=120)
    retry_window_seconds: int = Field(default=900, ge=1, le=900)
    max_concurrent: int = Field(default=1, ge=1, le=20)
    max_output_tokens_total: int = Field(default=49152, ge=256, le=131072)
    budget_microusd: int = Field(default=0, ge=0, le=1000000000)
    circuit_threshold: int = Field(default=3, ge=2, le=20)
    circuit_seconds: int = Field(default=60, ge=5, le=86400)

    @model_validator(mode='after')
    def unique_routes(self):
        if len({r.provider for r in self.routes}) != len(self.routes):
            raise ValueError('Mỗi provider chỉ xuất hiện một lần trong thứ tự')
        if self.budget_microusd and any(r.reservation_microusd <= 0 for r in self.routes):
            raise ValueError('Ngân sách USD cần mức đặt trước cho từng provider; không tự đoán giá')
        if self.fallback_enabled and any(r.provider not in self.allowed_providers for r in self.routes):
            raise ValueError('Phải cho phép từng provider nhận dữ liệu trước khi bật fallback')
        return self


def control_lock(session):
    session.execute(text('SELECT pg_advisory_xact_lock(721003)'))


def default_policy():
    s=get_settings()
    names=[s.llm_provider] if s.llm_provider in ('gemini','deepseek') else ['gemini']
    names += [name for name in ('gemini','deepseek') if name not in names]
    return Policy(routes=[Route(provider=n,model=getattr(s,f'{n}_model')) for n in names]).model_dump()


def get_policy(session):
    control_lock(session)
    row=session.get(LLMPolicy,1)
    if not row:
        row=LLMPolicy(id=1,data=default_policy());session.add(row);session.flush()
    return Policy(**row.data).model_dump()


def seed_credentials(session):
    control_lock(session)
    if session.scalar(select(LLMCredential.id).limit(1)):
        return
    s=get_settings()
    for provider in ('gemini','deepseek'):
        session.add(LLMCredential(name=f'{provider} mặc định',provider=provider,project_id='chưa-khai-báo',
                    secret_ref=f'env:{provider.upper()}_API_KEY',quota_group=f'{provider}-default',
                    allowed_models=[getattr(s,f'{provider}_model')]))
    session.flush()


def sync_credentials(session):
    """Worker-only secret lookup; only masked suffixes leave this process."""
    seed_credentials(session)
    for credential in session.scalars(select(LLMCredential).order_by(LLMCredential.id)):
        key=resolve(credential.secret_ref)
        credential.masked_suffix=mask(key)
        credential.checked_at=utcnow()
        if credential.state in ('unchecked','ready','missing_secret'):
            credential.state='ready' if key else 'missing_secret'
            credential.error=None if key else 'Worker chưa đọc được secret theo tên tham chiếu'


def capture_routing(session, provider):
    policy=get_policy(session)
    if provider=='fake':
        return {**policy,'routes':[dict(provider='fake',model='fake-v1',reservation_microusd=0)],'fallback_enabled':False}
    route=next((r for r in policy['routes'] if r['provider']==provider),None)
    if not route or provider not in policy['allowed_providers']:
        from fastapi import HTTPException
        raise HTTPException(422,'Provider chưa được cấu hình/cho phép nhận dữ liệu')
    index=policy['routes'].index(route)
    # Explicit primary provider wins; fallback only goes forward in admin order.
    return {**policy,'routes':copy.deepcopy(policy['routes'][index:])}


def quota_for(session, credential, model):
    row=session.scalar(select(LLMQuotaState).where(LLMQuotaState.provider==credential.provider,
        LLMQuotaState.quota_group==credential.quota_group,LLMQuotaState.model==model))
    if not row:
        row=LLMQuotaState(provider=credential.provider,quota_group=credential.quota_group,model=model)
        session.add(row);session.flush()
    return row


def health_for(session, provider, model):
    row=session.scalar(select(LLMProviderHealth).where(LLMProviderHealth.provider==provider,LLMProviderHealth.model==model))
    if not row:
        row=LLMProviderHealth(provider=provider,model=model);session.add(row);session.flush()
    return row


def advance_route(job, current_policy, reason):
    policy=job.routing
    if (job.kind!='script' or not policy.get('fallback_enabled') or not current_policy['fallback_enabled']
        or reason not in policy.get('fallback_on',[]) or reason not in current_policy['fallback_on']):
        return False
    routes=policy['routes']
    for index in range(job.route_index+1,len(routes)):
        route=routes[index]
        if route['provider'] in current_policy['allowed_providers'] and any(
            r['provider']==route['provider'] and r['model']==route['model'] for r in current_policy['routes']):
            previous=f'{job.provider}/{job.model}'
            job.route_index=index;job.provider=route['provider'];job.model=route['model'];job.credential_id=None
            job.logs=[*job.logs,dict(at=utcnow().isoformat(),action='fallback',reason=reason,
                previous=previous,provider=job.provider,model=job.model)]
            return True
    return False


def defer(job, status, reason, until=None):
    job.status=status;job.error_kind=reason
    job.error={'quota':'Đang chờ quota; bộ đếm nội bộ không phải quota thực của nhà cung cấp.',
               'rate_limit':'Đang chờ giới hạn tốc độ theo nhóm quota.',
               'circuit_open':'Provider/model tạm ngừng; chờ một request thử phục hồi.',
               'configuration_error':'Không có credential/model hợp lệ. Kiểm tra API key/secret và cấu hình.',
               'budget':'Đã chạm giới hạn lần gửi/token/ngân sách đặt trước của job.'}.get(reason,reason)
    if reason=='retry_deadline':job.error='Đã hết cửa sổ retry 15 phút. Hãy kiểm tra lỗi và tạo yêu cầu mới nếu cần.'
    if status=='retry_wait' and job.retry_started_at:
        deadline=job.retry_started_at+timedelta(seconds=job.routing.get('retry_window_seconds',900))
        until=min(until,deadline) if until else deadline
    job.next_attempt_at=until;job.owner=None;job.lease_until=None
    if status=='failed':job.finished_at=utcnow()


def reserve_attempt(session, job):
    """Return a secret to the worker only, or persist a wait/failure without a send."""
    current=get_policy(session)
    if not job.routing:
        job.routing=capture_routing(session,job.provider)
    policy=job.routing
    if retry_expired(job):
        defer(job,'failed','retry_deadline');return None
    if job.attempts>=min(6,policy['max_attempts'],current['max_attempts']):
        defer(job,'failed','budget');return None
    if job.reserved_output_tokens+job.output_limit>min(policy['max_output_tokens_total'],current['max_output_tokens_total']):
        defer(job,'failed','budget');return None
    if job.provider=='fake':
        return persist_reservation(session,job,None,'',None,None,0)
    sync_credentials(session)
    while True:
        if job.provider not in current['allowed_providers'] or (job.route_index>0 and not current['fallback_enabled']):
            defer(job,'failed','configuration_error');return None
        route=policy['routes'][job.route_index]
        if job.kind!='connection_test' and not any(r['provider']==job.provider and r['model']==job.model for r in current['routes']):
            defer(job,'failed','configuration_error');return None
        cost=route.get('reservation_microusd',0)
        budgets=[b for b in (policy['budget_microusd'],current['budget_microusd']) if b]
        if budgets and (cost<=0 or job.reserved_microusd+cost>min(budgets)):
            defer(job,'failed','budget');return None
        now=utcnow();health=health_for(session,job.provider,job.model)
        health_wait=None
        if health.state=='half_open' and health.probe_until and health.probe_until>now:
            health_wait=health.probe_until
        elif health.state=='open' and health.next_attempt_at and health.next_attempt_at>now:
            health_wait=health.next_attempt_at
        if health_wait:
            if advance_route(job,current,'service_error'):continue
            defer(job,'retry_wait','circuit_open',health_wait);return None
        candidates=session.scalars(select(LLMCredential).where(LLMCredential.provider==job.provider,
            LLMCredential.enabled.is_(True),LLMCredential.state=='ready').order_by(
                LLMCredential.priority,LLMCredential.last_used_at.asc().nullsfirst(),LLMCredential.id)).all()
        candidates=[c for c in candidates if job.model in c.allowed_models]
        if job.test_credential_id:
            candidates=[c for c in candidates if c.id==job.test_credential_id]
        # Pin retries after a service error; do not cycle keys to fight a 503.
        last_attempt=session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==job.id,LLMAttempt.number==job.attempts)) if job.attempts else None
        if job.credential_id and (job.error_kind in ('service_error','circuit_open') or
                                 (last_attempt and last_attempt.error_kind=='service_error' and last_attempt.provider==job.provider)):
            candidates=[c for c in candidates if c.id==job.credential_id]
        waiting=[];quota_block=False
        for credential in candidates:
            shared=session.scalar(select(LLMQuotaState).where(LLMQuotaState.provider==credential.provider,
                LLMQuotaState.quota_group==credential.quota_group,LLMQuotaState.model=='*'))
            if shared and shared.blocked:
                quota_block=True
                if shared.next_attempt_at:waiting.append(shared.next_attempt_at)
                continue
            quota=quota_for(session,credential,job.model)
            if quota.window_seconds and now>=quota.window_started_at+timedelta(seconds=quota.window_seconds):
                quota.window_started_at=now;quota.window_used=0
            if quota.blocked:
                # Confirmed exhaustion has no invented reset time.
                quota_block=True
                if quota.next_attempt_at and quota.next_attempt_at<=now:
                    quota.blocked=False
                else:
                    if quota.next_attempt_at:waiting.append(quota.next_attempt_at)
                    continue
            if quota.next_attempt_at and quota.next_attempt_at>now:
                waiting.append(quota.next_attempt_at);continue
            if quota.configured_limit is not None and quota.window_used>=quota.configured_limit:
                quota_block=True
                if quota.window_seconds:waiting.append(quota.window_started_at+timedelta(seconds=quota.window_seconds))
                continue
            # Shared quota group is at least as strict as project/model concurrency.
            active=session.scalars(select(LLMAttempt).where(LLMAttempt.provider==job.provider,
                LLMAttempt.model==job.model,LLMAttempt.quota_group==credential.quota_group,
                LLMAttempt.reservation_until>now,LLMAttempt.status.in_(('dispatched','unknown_outcome')))).all()
            if len(active)>=min(policy.get('max_concurrent',1),current['max_concurrent']):
                waiting.append(min(a.reservation_until for a in active));continue
            key=resolve(credential.secret_ref)
            if not key:continue
            return persist_reservation(session,job,credential,key,quota,health,cost)
        if quota_block and advance_route(job,current,'quota'):continue
        if not candidates:
            defer(job,'failed','configuration_error')
        else:
            defer(job,'waiting_quota' if quota_block else 'retry_wait','quota' if quota_block else 'rate_limit',min(waiting) if waiting else None)
        return None


def persist_reservation(session,job,credential,key,quota,health,cost):
    now=utcnow()
    if not job.retry_started_at:job.retry_started_at=now
    if quota:
        quota.window_used+=1;quota.observed_calls+=1
        credential.last_used_at=now;job.credential_id=credential.id
    if health and health.state!='closed':
        health.state='half_open';health.probe_job_id=job.id
        health.probe_until=now+timedelta(seconds=job.timeout_seconds+30)
    job.attempts+=1;job.dispatched_at=now
    job.reserved_output_tokens+=job.output_limit;job.reserved_microusd+=cost
    reason=next((x.get('reason','') for x in reversed(job.logs) if x.get('action')=='fallback'),'') if job.route_index else 'Nhà cung cấp được chọn'
    attempt=LLMAttempt(job_id=job.id,number=job.attempts,provider=job.provider,model=job.model,
        credential_id=credential.id if credential else None,quota_group=credential.quota_group if credential else None,
        reason=reason,reserved_microusd=cost,reserved_output_tokens=job.output_limit,
        reservation_until=now+timedelta(seconds=job.timeout_seconds+30),
        trace={k:v for k,v in job.prepared_requests.get(job.provider+'/'+job.model,{}).items() if k!='payload'})
    session.add(attempt);session.flush()
    return key


def finish_attempt(session,job,result=None,error=None):
    control_lock(session)
    attempt=session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==job.id,LLMAttempt.number==job.attempts))
    if not attempt or attempt.status!='dispatched':return
    attempt.finished_at=utcnow()
    attempt.latency=result.elapsed if result else error.elapsed
    attempt.usage=json_safe(result.usage if result else error.usage)
    attempt.request_id=redact_secrets(result.request_id if result else error.request_id)[:300] if (result.request_id if result else error.request_id) else None
    attempt.http_status=200 if result else error.status
    attempt.status='response' if result else ('unknown_outcome' if error.uncertain else 'failed')
    attempt.error_kind=None if result else error.kind
    attempt.error=None if result else redact_secrets(str(error))
    attempt.raw_output=redact_secrets(result.raw if result else error.raw) or None
    attempt.trace={**attempt.trace,**json_safe((result if result else error).diagnostics)}
    attempt.trace={**attempt.trace,'retryable':bool(error and error.retryable),
                   'retry_after_seconds':error.retry_after if error else None,
                   'usage_available':bool(attempt.usage)}
    if job.provider=='fake':return
    health=health_for(session,job.provider,job.model)
    if error and error.kind=='service_error':
        health.failures+=1;health.error=attempt.error
        if health.failures>=job.routing['circuit_threshold'] or health.state=='half_open':
            health.state='open';health.next_attempt_at=utcnow()+timedelta(seconds=job.routing['circuit_seconds'])
            health.probe_job_id=None;health.probe_until=None
    elif health.state=='closed' or health.probe_job_id==job.id:
        if error and error.uncertain:
            health.state='open';health.next_attempt_at=utcnow()+timedelta(seconds=job.routing['circuit_seconds'])
        else:
            health.failures=0;health.state='closed';health.error=None;health.next_attempt_at=None
        health.probe_job_id=None;health.probe_until=None
    credential=session.get(LLMCredential,attempt.credential_id) if attempt.credential_id else None
    if error and credential:
        if error.kind=='invalid_key':
            credential.enabled=False;credential.state='invalid';credential.error=attempt.error
        elif error.kind=='configuration_error':
            # Model errors do not disable a key for its other allowed models.
            credential.error=attempt.error
        if error.kind in ('daily_quota','billing_quota','rate_limit'):
            quota=quota_for(session,credential,job.model)
            quota.error=attempt.error
            if error.kind in ('daily_quota','billing_quota'):
                quota.blocked=True;quota.next_attempt_at=None
                if error.kind=='billing_quota':
                    shared=quota_for(session,credential,'*')
                    shared.blocked=True;shared.next_attempt_at=None;shared.error=attempt.error
            else:
                quota.next_attempt_at=utcnow()+timedelta(seconds=retry_delay(job,error))


def json_safe(value):
    import json
    return json.loads(redact_secrets(json.dumps(value,ensure_ascii=False)))


def retry_delay(job,error):
    policy=job.routing or {}
    cap=policy.get('retry_cap_seconds',120)
    backoff=min(cap,policy.get('retry_base_seconds',15)*2**max(0,job.attempts-1)+random.uniform(0,2))
    # Never cap Retry-After to an earlier time.
    return max(backoff,error.retry_after or 0)


def retry_expired(job, when=None):
    return bool(job.retry_started_at and (when or utcnow())>=job.retry_started_at+
                timedelta(seconds=job.routing.get('retry_window_seconds',900)))


def schedule_failure(session,job,error):
    current=get_policy(session)
    job.error=redact_secrets(str(error));job.error_kind=error.kind
    if error.uncertain:
        job.status='unknown_outcome';job.finished_at=utcnow()
    elif job.attempts>=min(6,job.routing.get('max_attempts',6),current['max_attempts']):
        job.status='failed';job.finished_at=utcnow()
    elif error.kind in ('service_error','rate_limit'):
        if error.kind=='service_error':advance_route(job,current,'service_error')
        job.status='retry_wait';job.next_attempt_at=utcnow()+timedelta(seconds=retry_delay(job,error))
    elif error.kind in ('daily_quota','billing_quota'):
        # Next reservation may use another independent group, then allowed fallback.
        job.status='waiting_quota';job.next_attempt_at=utcnow()+timedelta(seconds=retry_delay(job,error))
    else:
        job.status='failed';job.finished_at=utcnow()
    if job.status=='retry_wait' and retry_expired(job,job.next_attempt_at):
        job.status='failed';job.finished_at=utcnow()
        job.error += ' Đã hết cửa sổ retry; không gửi sớm hơn Retry-After. Có thể tạo yêu cầu mới thủ công.'
    attempt=session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==job.id,LLMAttempt.number==job.attempts))
    if attempt:
        attempt.trace={**attempt.trace,'retry_scheduled':job.status in ('retry_wait','waiting_quota'),
            'next_attempt_at':job.next_attempt_at.isoformat() if job.status in ('retry_wait','waiting_quota') and job.next_attempt_at else None,
            'retry_reason':job.error_kind}
    job.owner=None;job.lease_until=None


def mark_interrupted(session,job):
    control_lock(session)
    attempt=session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==job.id,LLMAttempt.status=='dispatched'))
    if attempt:
        attempt.status='unknown_outcome';attempt.error_kind='unknown_outcome'
        attempt.error=job.error;attempt.finished_at=utcnow()


def attempt_json(attempt):
    return {name:(getattr(attempt,name).isoformat() if getattr(attempt,name) is not None and name.endswith('_at') else getattr(attempt,name))
            for name in ('id','number','credential_id','provider','model','quota_group','status','reason','http_status',
                         'request_id','latency','usage','error_kind','error','trace','reserved_microusd','reserved_output_tokens','started_at','finished_at')}


def wake_quota_jobs(provider,model):
    # Separate transaction: never acquire job locks while holding the control lock.
    from app.database import session_scope
    from sqlalchemy import update
    with session_scope() as session:
        query=update(ScriptJob).where(ScriptJob.provider==provider,ScriptJob.status=='waiting_quota',ScriptJob.cancelled.is_(False))
        if model!='*':query=query.where(ScriptJob.model==model)
        session.execute(query.values(next_attempt_at=utcnow()))
