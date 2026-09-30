import copy
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from app.database import session_scope
from app.models import LLMCredential, LLMQuotaState, LLMProviderHealth, LLMAttempt, LLMPolicy, ScriptJob, utcnow
from app.services import llm_control as control
from app.services.script_service import PROMPT, ACTIVE, digest, interrupt_job
from app.llm.secrets import valid_reference

router=APIRouter()


class CredentialInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    provider:Literal['gemini','deepseek','groq']
    project_id:str=Field(min_length=1,max_length=200)
    secret_ref:str=Field(min_length=1,max_length=200)
    quota_group:str=Field(min_length=1,max_length=200)
    allowed_models:list[str]=Field(min_length=1,max_length=30)
    priority:int=Field(default=100,ge=0,le=10000)

    @model_validator(mode='after')
    def references(self):
        import re
        if not valid_reference(self.secret_ref):
            raise ValueError('Chỉ nhận env:GROQ_API_KEY, env:GEMINI_API_KEY, env:DEEPSEEK_API_KEY, env:LLM_KEY_TEN hoặc secret:llm_ten; không nhập key')
        if any(not re.fullmatch(r'[a-zA-Z0-9._/-]{1,200}',m) for m in self.allowed_models):
            raise ValueError('Tên model không hợp lệ')
        return self


def credential_json(c):
    return {name:getattr(c,name) for name in ('id','name','provider','project_id','secret_ref','quota_group','allowed_models','priority','enabled','state','masked_suffix','error')}


def get_credential(session,credential_id):
    c=session.get(LLMCredential,credential_id)
    if not c:raise HTTPException(404,'Không tìm thấy credential')
    return c


@router.get('/llm')
def dashboard(request:Request):
    from app.main import templates
    with session_scope() as session:
        policy=control.get_policy(session);control.seed_credentials(session)
        credentials=session.scalars(select(LLMCredential).order_by(LLMCredential.provider,LLMCredential.priority,LLMCredential.id)).all()
        quotas=session.scalars(select(LLMQuotaState).order_by(LLMQuotaState.id)).all()
        health=session.scalars(select(LLMProviderHealth).order_by(LLMProviderHealth.id)).all()
        jobs=session.scalars(select(ScriptJob).order_by(ScriptJob.id.desc()).limit(30)).all()
        if request.query_params.get('format')=='json':
            return {'policy':policy,'credentials':[credential_json(c) for c in credentials],
                    'quotas':[dict(id=q.id,provider=q.provider,quota_group=q.quota_group,model=q.model,
                                  configured_limit=q.configured_limit,window_seconds=q.window_seconds,
                                  window_used=q.window_used,observed_calls=q.observed_calls,blocked=q.blocked,
                                  next_attempt_at=q.next_attempt_at,error=q.error) for q in quotas],
                    'health':[dict(provider=h.provider,model=h.model,failures=h.failures,state=h.state,next_attempt_at=h.next_attempt_at,error=h.error) for h in health]}
        return templates.TemplateResponse(request=request,name='llm/dashboard.html',context=dict(policy=policy,credentials=credentials,quotas=quotas,health=health,jobs=jobs))


@router.post('/llm/policy')
def save_policy(payload:control.Policy):
    with session_scope() as session:
        control.get_policy(session)
        row=session.get(LLMPolicy,1);row.data=payload.model_dump();row.updated_at=utcnow()
        return {'saved':True}


@router.post('/llm/credentials')
def add_credential(payload:CredentialInput):
    with session_scope() as session:
        control.control_lock(session)
        if session.scalar(select(LLMCredential.id).where((LLMCredential.name==payload.name)|(LLMCredential.secret_ref==payload.secret_ref))):
            raise HTTPException(409,'Tên hoặc tham chiếu secret đã tồn tại')
        peers=session.scalars(select(LLMCredential).where(LLMCredential.provider==payload.provider,LLMCredential.project_id==payload.project_id)).all()
        if any(c.quota_group!=payload.quota_group for c in peers):
            raise HTTPException(422,'Các key cùng project phải dùng cùng quota group')
        credential=LLMCredential(**payload.model_dump());session.add(credential);session.flush()
        return credential_json(credential)


@router.post('/llm/credentials/{credential_id}')
def edit_credential(credential_id:int,payload:CredentialInput):
    with session_scope() as session:
        control.control_lock(session);credential=get_credential(session,credential_id)
        # Quota identity cannot be changed after any reservation: doing so would erase its effective history.
        if session.scalar(select(LLMAttempt.id).where(LLMAttempt.credential_id==credential_id).limit(1)) and any(
            getattr(credential,k)!=getattr(payload,k) for k in ('provider','secret_ref','quota_group')):
            raise HTTPException(409,'Credential đã có lịch sử: giữ nguyên provider/secret/quota group để không mất bộ đếm; project vẫn có thể bổ sung')
        peers=session.scalars(select(LLMCredential).where(LLMCredential.id!=credential_id)).all()
        if any(c.name==payload.name or c.secret_ref==payload.secret_ref for c in peers):
            raise HTTPException(409,'Tên hoặc tham chiếu đã tồn tại')
        if any(c.provider==payload.provider and c.project_id==payload.project_id and c.quota_group!=payload.quota_group for c in peers):
            raise HTTPException(422,'Các key cùng project phải dùng chung quota group')
        for k,v in payload.model_dump().items():setattr(credential,k,v)
        credential.state='unchecked';credential.error=None
        return credential_json(credential)


class ToggleInput(BaseModel):
    enabled:bool


@router.post('/llm/credentials/{credential_id}/toggle')
def toggle_credential(credential_id:int,payload:ToggleInput):
    with session_scope() as session:
        control.control_lock(session);credential=get_credential(session,credential_id)
        credential.enabled=payload.enabled
        if payload.enabled:credential.state='unchecked';credential.error=None
        return {'enabled':credential.enabled}


class QuotaInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    credential_id:int
    model:str=Field(min_length=1,max_length=200)
    configured_limit:int|None=Field(default=None,ge=1,le=10000000)
    window_seconds:int|None=Field(default=None,ge=1,le=31536000)


@router.post('/llm/quotas')
def configure_quota(payload:QuotaInput):
    with session_scope() as session:
        control.control_lock(session);credential=get_credential(session,payload.credential_id)
        if payload.model not in credential.allowed_models:raise HTTPException(422,'Model không thuộc credential')
        quota=control.quota_for(session,credential,payload.model)
        quota.configured_limit=payload.configured_limit;quota.window_seconds=payload.window_seconds
        # No counter reset on editing a limit.
        result={'id':quota.id};provider=quota.provider;model=quota.model
    control.wake_quota_jobs(provider,model)
    return result


class ResetInput(BaseModel):
    confirmed_dashboard:bool


@router.post('/llm/quotas/{quota_id}/release')
def release_quota(quota_id:int,payload:ResetInput):
    if not payload.confirmed_dashboard:raise HTTPException(422,'Cần đối chiếu dashboard trước khi mở lại quota')
    with session_scope() as session:
        control.control_lock(session);q=session.get(LLMQuotaState,quota_id)
        if not q:raise HTTPException(404,'Không tìm thấy quota')
        q.blocked=False;q.next_attempt_at=None;q.error=None
        # observed_calls is lifetime conservative count; never erased by release.
        q.window_used=0;q.window_started_at=utcnow()
        result={'released':True,'observed_calls':q.observed_calls};provider=q.provider;model=q.model
    control.wake_quota_jobs(provider,model)
    return result


class ConnectionInput(BaseModel):
    model:str=Field(min_length=1,max_length=200)
    acknowledge_cost:bool=False
    idempotency_key:str=Field(min_length=8,max_length=128)


@router.post('/llm/credentials/{credential_id}/test',status_code=202)
def test_connection(credential_id:int,payload:ConnectionInput):
    if not payload.acknowledge_cost:raise HTTPException(422,'Thử kết nối sẽ gọi sinh nội dung, có thể tốn quota/chi phí')
    with session_scope() as session:
        control.control_lock(session)
        credential=get_credential(session,credential_id)
        request_hash=digest(dict(credential_id=credential_id,**payload.model_dump()))
        existing=session.scalar(select(ScriptJob).where(ScriptJob.idempotency_key==payload.idempotency_key))
        if existing:
            if existing.request_hash!=request_hash:raise HTTPException(409,'Idempotency key đã dùng cho yêu cầu khác')
            return {'id':existing.id}
        if not credential.enabled or payload.model not in credential.allowed_models:raise HTTPException(422,'Credential/model chưa được bật')
        if session.scalar(select(ScriptJob.id).where(ScriptJob.test_credential_id==credential_id,ScriptJob.status.in_(ACTIVE))):
            raise HTTPException(409,'Credential đang có yêu cầu thử kết nối')
        routing=control.capture_routing(session,credential.provider)
        routing['fallback_enabled']=False;routing['max_attempts']=1;routing['routes']=routing['routes'][:1]
        routing['routes'][0]['model']=payload.model
        from app.config import get_settings
        s=get_settings()
        job=ScriptJob(kind='connection_test',test_credential_id=credential_id,provider=credential.provider,model=payload.model,
                      routing=routing,idempotency_key=payload.idempotency_key,request_hash=request_hash,tone='neutral',
                      target_seconds=15,timeout_seconds=s.llm_timeout_seconds,output_limit=min(1024,s.llm_max_output_tokens),
                      prompt_text=PROMPT.read_text(encoding='utf-8'))
        session.add(job);session.flush()
        from app.services.script_service import prepare_requests, connection_input
        prepare_requests(job, connection_input(job))
        return {'id':job.id}


@router.get('/llm/jobs/{job_id}')
def job_detail(request:Request,job_id:int):
    from app.main import templates
    from app.routes.scripts import job_json
    with session_scope() as session:
        job=session.get(ScriptJob,job_id)
        if not job:raise HTTPException(404,'Không tìm thấy job')
        attempts=session.scalars(select(LLMAttempt).where(LLMAttempt.job_id==job.id).order_by(LLMAttempt.number)).all()
        if request.query_params.get('format')=='json':return {'job':job_json(job),'attempts':[control.attempt_json(a) for a in attempts]}
        return templates.TemplateResponse(request=request,name='llm/job.html',context=dict(job=job,attempts=attempts))


class RetryInput(BaseModel):
    acknowledge_unknown:bool=False


@router.post('/llm/jobs/{job_id}/retry')
def retry_job(job_id:int,payload:RetryInput):
    with session_scope() as session:
        job=session.scalar(select(ScriptJob).where(ScriptJob.id==job_id).with_for_update())
        if not job:raise HTTPException(404,'Không tìm thấy job')
        if job.status in ('running','succeeded','queued','retry_wait'):raise HTTPException(409,'Job đang chạy/đã xong/đã xếp lịch')
        if job.attempts>=min(6,job.routing.get('max_attempts',6)) or control.retry_expired(job):raise HTTPException(409,'Đã hết lượt/thời gian của job; cần tạo yêu cầu mới')
        if job.error_kind in ('validation_error','insufficient_evidence','output_truncated','empty_response','content_refusal','response_parse_error'):raise HTTPException(409,'Cần kiểm tra nội dung và tạo yêu cầu mới, không retry cùng job')
        if job.next_attempt_at and job.next_attempt_at>utcnow():raise HTTPException(409,'Chưa đến thời điểm được thử lại; không bỏ qua Retry-After')
        if job.status=='unknown_outcome' and not payload.acknowledge_unknown:
            raise HTTPException(422,'Lần trước có thể đã tính phí. Cần xác nhận chủ động trước khi thử lại')
        if job.event_id and session.scalar(select(ScriptJob.id).where(ScriptJob.event_id==job.event_id,ScriptJob.id!=job.id,ScriptJob.status.in_(ACTIVE))):
            raise HTTPException(409,'Nhóm tin đã có job khác đang hoạt động')
        job.status='queued';job.next_attempt_at=utcnow();job.cancelled=False;job.finished_at=None
        job.logs=[*job.logs,dict(at=utcnow().isoformat(),action='manual_retry',unknown_acknowledged=payload.acknowledge_unknown)]
        return {'id':job.id}


@router.post('/llm/jobs/{job_id}/cancel')
def cancel_job(job_id:int):
    with session_scope() as session:
        job=session.scalar(select(ScriptJob).where(ScriptJob.id==job_id).with_for_update())
        if not job:raise HTTPException(404,'Không tìm thấy job')
        if job.status not in ACTIVE:raise HTTPException(409,'Job đã kết thúc')
        if job.status=='running' and job.dispatched_at:
            interrupt_job(job,'Người dùng hủy sau khi đã gửi; không thể thu hồi request')
            control.mark_interrupted(session,job)
        else:
            job.status='failed';job.error_kind='cancelled';job.error='Người dùng đã hủy job trước lần gửi tiếp theo'
            job.finished_at=utcnow();job.owner=None;job.lease_until=None
        job.cancelled=True;job.next_attempt_at=None
        return {'id':job.id,'status':job.status}
