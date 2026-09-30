import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid
import pytest
from sqlalchemy import select, func
from app.models import (Source,Article,Event,ScriptJob,ScriptVersion,LLMCredential,LLMQuotaState,LLMProviderHealth,LLMAttempt,LLMPolicy,utcnow)
from app.config import get_settings
from app.llm.base import FakeProvider,ProviderFailure
from app.llm.failures import classify
from app.llm.secrets import valid_reference,mask
from app.services import llm_control as control
from app.services.script_service import CreateRequest,enqueue_script,claim_script,execute_script,recover_scripts


@pytest.fixture
def lab(db,monkeypatch):
    for index in range(1,5):monkeypatch.setenv(f'LLM_KEY_TEST_{index}',f'synthetic-test-key-{index:04d}')
    with db() as session:
        policy=control.get_policy(session)
        gm=next(r['model'] for r in policy['routes'] if r['provider']=='gemini')
        dm=next(r['model'] for r in policy['routes'] if r['provider']=='deepseek')
        creds=[LLMCredential(name='Gemini A',provider='gemini',project_id='g-project',secret_ref='env:LLM_KEY_TEST_1',quota_group='shared',allowed_models=[gm]),
               LLMCredential(name='Gemini B',provider='gemini',project_id='g-project',secret_ref='env:LLM_KEY_TEST_2',quota_group='shared',allowed_models=[gm]),
               LLMCredential(name='DeepSeek',provider='deepseek',project_id='d-project',secret_ref='env:LLM_KEY_TEST_3',quota_group='deepseek-account',allowed_models=[dm])]
        session.add_all(creds);session.flush();ids=[c.id for c in creds]
        source=Source(name='Quota fixture',url='https://example.com/quota',enabled=False);session.add(source);session.flush();source_id=source.id
    def enqueue(provider='gemini'):
        with db() as session:
            event=Event(title='Tin giả lập kiểm tra quota',decision='selected');session.add(event);session.flush()
            session.add(Article(source_id=source_id,event_id=event.id,title=event.title,
                canonical_url='https://example.com/'+uuid.uuid4().hex,fingerprint=uuid.uuid4().hex,
                summary='Thư viện thành phố mở thêm phòng đọc với 120 chỗ ngồi. Khu vực mới phục vụ bạn đọc vào các ngày trong tuần.'))
            return enqueue_script(session,CreateRequest(event_id=event.id,provider=provider,idempotency_key=uuid.uuid4().hex)).id
    return dict(enqueue=enqueue,ids=ids,gm=gm,dm=dm)


def execute(db,job_id):
    with db() as session:
        job=session.get(ScriptJob,job_id)
        if job.next_attempt_at:job.next_attempt_at=utcnow()-timedelta(seconds=1)
    with db() as session:claim=claim_script(session)
    assert claim and claim[0]==job_id
    execute_script(*claim)


def adapter(monkeypatch,failures=()):
    calls=[];queue=list(failures)
    class Mock(FakeProvider):
        def generate_script(self,data,prompt,feedback=''):
            calls.append(dict(key=self.key,model=self.model,prompt=prompt,sources=data.model_dump(mode='json')))
            if queue:
                error=queue.pop(0)
                if error:raise error
            return super().generate_script(data,prompt,feedback)
    monkeypatch.setattr('app.services.script_service.get_provider',lambda name,model,*args:Mock(model))
    return calls


def update_policy(db,**values):
    with db() as session:
        p=control.get_policy(session);p.update(values);session.get(LLMPolicy,1).data=control.Policy(**p).model_dump()


def reserve(db,job_id):
    with db() as session:
        job=session.scalar(select(ScriptJob).where(ScriptJob.id==job_id).with_for_update())
        return control.reserve_attempt(session,job)


def test_round_robin_filters_disabled_model_and_priority(db,lab,monkeypatch):
    calls=adapter(monkeypatch)
    with db() as session:
        session.add(LLMCredential(name='Wrong model',provider='gemini',project_id='other',secret_ref='env:LLM_KEY_TEST_4',quota_group='other',allowed_models=['not-this-model'],priority=0))
    for _ in range(4):execute(db,lab['enqueue']())
    assert [c['key'] for c in calls]==['synthetic-test-key-0001','synthetic-test-key-0002']*2
    with db() as session:session.get(LLMCredential,lab['ids'][0]).enabled=False
    execute(db,lab['enqueue']());assert calls[-1]['key'].endswith('0002')
    with db() as session:
        assert len(session.scalars(select(LLMQuotaState).where(LLMQuotaState.provider=='gemini')).all())==1
        q=session.scalar(select(LLMQuotaState).where(LLMQuotaState.provider=='gemini'))
        assert q.configured_limit is None and q.observed_calls==5


def test_two_workers_reserve_only_one_last_shared_quota(db,lab):
    with db() as session:
        q=control.quota_for(session,session.get(LLMCredential,lab['ids'][0]),lab['gm']);q.configured_limit=1
    ids=[lab['enqueue'](),lab['enqueue']()]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda job_id:reserve(db,job_id),ids))
    assert sum(value is not None for value in results)==1
    with db() as session:
        q=session.scalar(select(LLMQuotaState));assert q.window_used==1 and q.observed_calls==1
        assert session.scalar(select(func.count()).select_from(LLMAttempt))==1
        waiting=session.scalar(select(ScriptJob).where(ScriptJob.status=='waiting_quota'))
        assert waiting.next_attempt_at is None and waiting.attempts==0


def test_503_retry_pins_key_and_honors_retry_after(db,lab,monkeypatch):
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503,retry_after=120)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        job=session.get(ScriptJob,job_id);assert (job.next_attempt_at-utcnow()).total_seconds()>118
        assert job.credential_id==lab['ids'][0]
    # Another job advances round-robin but retry still uses the original key.
    execute(db,lab['enqueue']())
    execute(db,job_id)
    assert [c['key'] for c in calls]==['synthetic-test-key-0001','synthetic-test-key-0002','synthetic-test-key-0001']
    with db() as session:
        assert session.get(ScriptJob,job_id).status=='succeeded'
        assert session.get(ScriptJob,job_id).attempts==2


def test_circuit_opens_and_only_one_half_open_probe(db,lab,monkeypatch):
    update_policy(db,circuit_threshold=2)
    adapter(monkeypatch,[ProviderFailure('busy',status=503)]*2)
    job=lab['enqueue']();execute(db,job);execute(db,job)
    with db() as session:
        h=session.scalar(select(LLMProviderHealth));assert h.state=='open' and h.failures==2
    blocked=lab['enqueue']();assert reserve(db,blocked) is None
    with db() as session:
        assert session.get(ScriptJob,blocked).attempts==0
        h=session.scalar(select(LLMProviderHealth));h.next_attempt_at=utcnow()-timedelta(seconds=1)
    ids=[lab['enqueue'](),lab['enqueue']()]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda j:reserve(db,j),ids))
    assert sum(r is not None for r in results)==1
    with db() as session:
        h=session.scalar(select(LLMProviderHealth));assert h.state=='half_open' and h.probe_job_id in ids
        assert h.probe_until>utcnow()


@pytest.mark.parametrize('kind', ['rate_limit','daily_quota'])
def test_429_daily_and_rate_differ_and_group_is_shared(db,lab,monkeypatch,kind):
    calls=adapter(monkeypatch,[ProviderFailure('quota error',status=429,kind=kind,retry_after=30)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        q=session.scalar(select(LLMQuotaState));assert q.blocked==(kind=='daily_quota')
        assert (q.next_attempt_at is None)==(kind=='daily_quota')
        assert all(c.enabled for c in session.scalars(select(LLMCredential)))
    execute(db,job_id)
    assert len(calls)==1  # does not move to the other key in the same group
    with db() as session:
        job=session.get(ScriptJob,job_id)
        assert job.status==('waiting_quota' if kind=='daily_quota' else 'retry_wait')
        if kind=='daily_quota':assert job.next_attempt_at is None


@pytest.mark.parametrize('kind,status,disabled',[('invalid_key',400,True),('configuration_error',403,False),('configuration_error',404,False)])
def test_key_and_permission_errors_no_loop(db,lab,monkeypatch,kind,status,disabled):
    calls=adapter(monkeypatch,[ProviderFailure('denied',status=status,kind=kind)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        c=session.get(LLMCredential,lab['ids'][0]);assert c.enabled!=disabled
        job=session.get(ScriptJob,job_id);assert job.status=='failed' and job.attempts==1
        assert claim_script(session) is None
    assert len(calls)==1


def test_fallback_order_snapshot_and_total_attempt_limit(db,lab,monkeypatch):
    update_policy(db,fallback_enabled=True,max_attempts=4)
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)]*3+[None])
    job_id=lab['enqueue']()
    for _ in range(4):execute(db,job_id)
    assert [c['model'] for c in calls]==[lab['gm']]*3+[lab['dm']]
    assert calls[0]['sources']==calls[1]['sources'] and calls[0]['prompt']==calls[1]['prompt']
    with db() as session:
        attempts=session.scalars(select(LLMAttempt).order_by(LLMAttempt.id)).all()
        assert [a.provider for a in attempts]==['gemini']*3+['deepseek']
        version=session.scalar(select(ScriptVersion));assert version.provider=='deepseek' and version.status=='needs_review'
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)]*4)
    with db() as session:
        h=session.scalar(select(LLMProviderHealth).where(LLMProviderHealth.provider=='gemini'))
        h.state='closed';h.failures=0;h.next_attempt_at=None
    another=lab['enqueue']()
    for _ in range(4):execute(db,another)
    with db() as session:
        job=session.get(ScriptJob,another);assert job.status=='failed' and job.attempts==4
    assert len(calls)==4


def test_fallback_disallowed_for_validation_and_timeout(db,lab,monkeypatch):
    update_policy(db,fallback_enabled=True)
    calls=adapter(monkeypatch,[ProviderFailure('truncated',raw='{"invalid":true}',kind='validation_error')])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        j=session.get(ScriptJob,job_id);assert j.provider=='gemini' and j.status=='failed' and j.attempts==1
    calls=adapter(monkeypatch,[ProviderFailure('timeout',uncertain=True)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        j=session.get(ScriptJob,job_id);assert j.provider=='gemini' and j.status=='unknown_outcome'
        a=session.scalar(select(LLMAttempt).where(LLMAttempt.job_id==j.id));assert a.status=='unknown_outcome'
        assert claim_script(session) is None


def test_fallback_revocation_budget_and_retry_count_not_reset(db,lab,monkeypatch,client):
    update_policy(db,fallback_enabled=True,max_output_tokens_total=8192)
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)])
    job_id=lab['enqueue']();execute(db,job_id);execute(db,job_id)
    assert len(calls)==1
    with db() as session:
        j=session.get(ScriptJob,job_id);assert j.error_kind=='budget' and j.attempts==1
    assert client.post(f'/llm/jobs/{job_id}/retry',json={}).status_code==200
    execute(db,job_id);assert len(calls)==1
    update_policy(db,max_output_tokens_total=32768)
    with db() as session:
        h=session.scalar(select(LLMProviderHealth).where(LLMProviderHealth.provider=='gemini'))
        h.state='closed';h.failures=0;h.next_attempt_at=None
    another=lab['enqueue']();calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)]*3)
    for _ in range(3):execute(db,another)
    update_policy(db,fallback_enabled=False);execute(db,another)
    assert len(calls)==3
    with db() as session:assert session.get(ScriptJob,another).error_kind=='configuration_error'


def test_dashboard_does_not_reflect_or_store_keys(db,lab,client,monkeypatch):
    calls=adapter(monkeypatch,[ProviderFailure('bad synthetic-test-key-0001',status=401)])
    job_id=lab['enqueue']();execute(db,job_id)
    for path in ('/llm','/llm?format=json',f'/llm/jobs/{job_id}',f'/llm/jobs/{job_id}?format=json',f'/script-jobs/{job_id}'):
        response=client.get(path);assert response.status_code==200,response.text
        assert 'synthetic-test-key' not in response.text
    response=client.post('/llm/credentials',json=dict(name='oops',provider='gemini',project_id='p',secret_ref='synthetic-test-key-0001',quota_group='q',allowed_models=['model']))
    assert response.status_code==422 and 'synthetic-test-key' not in response.text
    with db() as session:
        a=session.scalar(select(LLMAttempt));assert '[REDACTED]' in a.error


def test_connection_test_requires_cost_ack_and_is_one_send(db,lab,client,monkeypatch):
    calls=adapter(monkeypatch)
    endpoint=f'/llm/credentials/{lab["ids"][0]}/test'
    payload=dict(model=lab['gm'],idempotency_key='connection-test-one')
    assert client.post(endpoint,json=payload).status_code==422
    payload['acknowledge_cost']=True
    result=client.post(endpoint,json=payload);assert result.status_code==202
    job_id=result.json()['id'];assert client.post(endpoint,json=payload).json()['id']==job_id
    execute(db,job_id)
    with db() as session:
        j=session.get(ScriptJob,job_id);assert j.kind=='connection_test' and j.attempts==1
        assert session.scalar(select(func.count()).select_from(ScriptVersion))==0
    assert len(calls)==1


def test_cancel_and_explicit_retry_unknown_are_fenced(db,lab,client):
    job_id=lab['enqueue']()
    with db() as session:claim=claim_script(session)
    reserve(db,job_id)
    assert client.post(f'/llm/jobs/{job_id}/cancel',json={}).json()['status']=='unknown_outcome'
    assert client.post(f'/llm/jobs/{job_id}/retry',json={}).status_code==422
    with db() as session:
        a=session.scalar(select(LLMAttempt));assert a.status=='unknown_outcome'
        assert claim_script(session) is None
    assert client.post(f'/llm/jobs/{job_id}/retry',json={'acknowledge_unknown':True}).status_code==200
    with db() as session:assert session.get(ScriptJob,job_id).attempts==1


def test_quota_manual_release_preserves_observed_count(db,lab,client):
    with db() as session:
        q=control.quota_for(session,session.get(LLMCredential,lab['ids'][0]),lab['gm'])
        q.blocked=True;q.observed_calls=8;q.window_used=8;quota_id=q.id
    assert client.post(f'/llm/quotas/{quota_id}/release',json={'confirmed_dashboard':False}).status_code==422
    assert client.post(f'/llm/quotas/{quota_id}/release',json={'confirmed_dashboard':True}).json()['observed_calls']==8
    with db() as session:
        q=session.get(LLMQuotaState,quota_id);assert not q.blocked and q.observed_calls==8 and q.window_used==0


@pytest.mark.parametrize('provider,status,body,expected',[
    ('gemini',403,{'error':{'status':'PERMISSION_DENIED'}},'configuration_error'),
    ('gemini',403,{'error':{'details':[{'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'API_KEY_INVALID'}]}},'invalid_key'),
    ('gemini',429,{'error':{'details':[{'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[{'quotaId':'GenerateRequestsPerDayPerProjectPerModel'}]}]}},'daily_quota'),
    ('gemini',429,{'error':{'message':'Quota exceeded'}},'rate_limit'),
    ('deepseek',401,{},'invalid_key'),('deepseek',402,{},'billing_quota'),('deepseek',429,{},'rate_limit'),
    ('gemini',503,{},'service_error')])
def test_conservative_error_classification(provider,status,body,expected):
    assert classify(provider,status,body)==expected


def test_secret_references_are_allowlisted():
    assert valid_reference('env:LLM_KEY_GEMINI_A') and valid_reference('secret:llm_gemini_a')
    assert not valid_reference('env:POSTGRES_PASSWORD') and not valid_reference('secret:../../etc/passwd')
    assert mask('short')=='••••' and mask('synthetic-long-key-1234')=='••••1234'


def test_successful_probe_closes_circuit(db,lab):
    from app.llm.base import Generation
    job_id=lab['enqueue']()
    with db() as session:
        h=control.health_for(session,'gemini',lab['gm']);h.state='open';h.failures=3
        h.next_attempt_at=utcnow()-timedelta(seconds=1)
    assert reserve(db,job_id)
    with db() as session:
        j=session.get(ScriptJob,job_id)
        control.finish_attempt(session,j,result=Generation('{}'))
    with db() as session:
        h=session.scalar(select(LLMProviderHealth));assert h.state=='closed' and h.failures==0 and h.probe_job_id is None


def test_usd_reservation_budget_stops_before_send(db,lab,monkeypatch):
    with db() as session:
        p=control.get_policy(session);p['fallback_enabled']=True;p['budget_microusd']=100
        for route in p['routes']:route['reservation_microusd']=60
        session.get(LLMPolicy,1).data=control.Policy(**p).model_dump()
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)])
    job_id=lab['enqueue']();execute(db,job_id);execute(db,job_id)
    with db() as session:
        j=session.get(ScriptJob,job_id);assert j.error_kind=='budget' and j.reserved_microusd==60 and j.attempts==1
    assert len(calls)==1


def test_daily_quota_fallback_and_billing_blocks_all_models(db,lab,monkeypatch):
    update_policy(db,fallback_enabled=True)
    calls=adapter(monkeypatch,[ProviderFailure('daily',status=429,kind='daily_quota'),None])
    job_id=lab['enqueue']();execute(db,job_id);execute(db,job_id)
    assert [c['model'] for c in calls]==[lab['gm'],lab['dm']]
    calls=adapter(monkeypatch,[ProviderFailure('balance',status=402,kind='billing_quota')])
    job_id=lab['enqueue']('deepseek');execute(db,job_id)
    with db() as session:
        shared=session.scalar(select(LLMQuotaState).where(LLMQuotaState.provider=='deepseek',LLMQuotaState.model=='*'))
        assert shared.blocked and shared.next_attempt_at is None


def test_configured_window_and_shared_project_guard(db,lab,client):
    with db() as session:
        q=control.quota_for(session,session.get(LLMCredential,lab['ids'][0]),lab['gm'])
        q.configured_limit=1;q.window_seconds=60;q.window_used=1;q.observed_calls=10
        q.window_started_at=utcnow()-timedelta(seconds=61)
    assert reserve(db,lab['enqueue']())
    with db() as session:
        q=session.scalar(select(LLMQuotaState));assert q.window_used==1 and q.observed_calls==11
    payload=dict(name='Wrong independent group',provider='gemini',project_id='g-project',secret_ref='env:LLM_KEY_TEST_4',quota_group='new-group',allowed_models=[lab['gm']])
    assert client.post('/llm/credentials',json=payload).status_code==422
