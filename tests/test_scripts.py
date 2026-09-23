import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select, func
from app.config import get_settings
from app.models import Source, Event, Article, ScriptJob, ScriptVersion, ScriptSourceSnapshot, ScriptReview, utcnow
from app.llm.base import FakeProvider, Generation, ProviderFailure, retry_after_seconds
from app.llm.gemini import GeminiProvider
from app.llm.deepseek import DeepSeekProvider
from app.llm.schemas import Input, validate_output
from app.services.script_service import (CreateRequest, enqueue_script, claim_script, execute_script,
    recover_scripts, fail_script_owned, renderable_version, save_version, review_version)

TEXT = 'Thư viện thành phố mở thêm phòng đọc với 120 chỗ ngồi. Đại diện thư viện cho biết khu vực mới phục vụ bạn đọc vào các ngày trong tuần.'


def input_data():
    return Input(schema_version='1.0', story_id='1', language='vi', target_seconds=45, tone='neutral',
                 sources=[dict(source_id='article-1', url='https://example.com/news', title='Phòng đọc mới', published_at=None, text=TEXT)])


def test_validator_quote_reference_duration_hook_and_refusal():
    data = input_data()
    output = json.loads(FakeProvider('fake').generate_script(data, '').raw)
    assert validate_output(data, json.dumps(output)).decision == 'draft'
    for mutate in [lambda p:p['claims'][0]['evidence'][0].update(quote='giá 999 triệu đồng'),
                   lambda p:p['claims'][0]['evidence'][0].update(source_id='missing'),
                   lambda p:p['scenes'][0].update(claim_ids=['missing']),
                   lambda p:p['scenes'][0].update(seconds=10),
                   lambda p:p.update(hook='Không khớp'),
                   lambda p:p.update(decision='insufficient_evidence')]:
        wrong = copy.deepcopy(output); mutate(wrong)
        with pytest.raises(ValueError): validate_output(data, json.dumps(wrong))


@pytest.mark.parametrize('provider', [GeminiProvider, DeepSeekProvider])
def test_adapter_success_and_truncation(provider):
    raw = FakeProvider('fake').generate_script(input_data(), '').raw
    body = {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':raw}]}}], 'usageMetadata':{'totalTokenCount':10}} if provider is GeminiProvider else {'choices':[{'finish_reason':'stop','message':{'content':raw}}], 'usage':{'total_tokens':10}}
    requests = []
    def handler(request):
        requests.append(json.loads(request.content)); return httpx.Response(200, json=body, headers={'x-request-id':'request-123'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=provider('model','secret',client=client)
        result=adapter.generate_script(input_data(),'prompt')
        assert result.raw==raw and result.usage and result.request_id=='request-123'
        if provider is GeminiProvider:
            assert requests[0]['generationConfig']['responseMimeType']=='application/json'
            body['candidates'][0]['finishReason']='MAX_TOKENS'
        else: body['choices'][0]['finish_reason']='length'
        with pytest.raises(ProviderFailure) as error: adapter.generate_script(input_data(),'prompt')
        assert error.value.raw and not error.value.retryable


@pytest.mark.parametrize('status,retryable', [(429,True),(500,True),(502,True),(503,True),(504,True),(401,False),(403,False),(400,False)])
def test_adapter_http_failures(status,retryable):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'error':'Unavailable'},headers={'retry-after':'17','x-request-id':'rid'}))) as client:
        with pytest.raises(ProviderFailure) as error: GeminiProvider('model','secret',client=client).generate_script(input_data(),'prompt')
    assert error.value.status==status and error.value.retryable==retryable
    assert error.value.retry_after==17 and error.value.request_id=='rid'


def test_adapter_timeout_not_retried():
    calls=[]
    def handler(request): calls.append(request); raise httpx.ReadTimeout('timeout')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderFailure) as error: GeminiProvider('model','secret',client=client).generate_script(input_data(),'prompt')
    assert error.value.uncertain and not error.value.retryable and len(calls)==1
    assert retry_after_seconds('invalid') is None


def test_adapter_redacts_key_and_retains_truncated_usage(monkeypatch):
    monkeypatch.setattr(get_settings(),'gemini_api_key','test-secret-key')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401,text='Invalid test-secret-key'))) as client:
        with pytest.raises(ProviderFailure) as error:GeminiProvider('model','test-secret-key',client=client).generate_script(input_data(),'prompt')
        assert 'test-secret-key' not in str(error.value) and '[REDACTED]' in str(error.value)
    body={'candidates':[{'finishReason':'MAX_TOKENS'}],'usageMetadata':{'totalTokenCount':50}}
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body))) as client:
        with pytest.raises(ProviderFailure) as error:GeminiProvider('model','test-secret-key',client=client).generate_script(input_data(),'prompt')
        assert error.value.usage['totalTokenCount']==50


@pytest.fixture
def selected_event(db,monkeypatch):
    monkeypatch.setattr(get_settings(),'llm_allow_fake',True)
    with db() as session:
        source=Source(name='Fixture',url='https://example.com/feed',enabled=False)
        event=Event(title='Phòng đọc mới <script>alert(1)</script>',decision='selected')
        session.add_all([source,event]);session.flush()
        session.add(Article(source_id=source.id,event_id=event.id,canonical_url='https://example.com/news',
                            title=event.title,summary=TEXT+' <script>window.injected=true</script>',fingerprint='1'*64))
        return event.id


def request_for(event_id,key='test-key-0001',**kwargs):
    return CreateRequest(event_id=event_id,provider='fake',idempotency_key=key,**kwargs)


def run_queued(db):
    with db() as session: claim=claim_script(session)
    with db() as session:
        state=[(j.id,j.status,j.next_attempt_at.isoformat(),utcnow().isoformat()) for j in session.scalars(select(ScriptJob))]
    assert claim, state
    execute_script(*claim)
    return claim


def test_double_create_concurrent_and_fake_full_review(db,client,selected_event):
    payload=request_for(selected_event).model_dump()
    def create():
        with db() as session:return enqueue_script(session,CreateRequest(**payload)).id
    with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(lambda _:create(),range(2)))
    assert ids[0]==ids[1]
    assert client.post('/script-jobs',json=payload).json()['id']==ids[0]
    other={**payload,'idempotency_key':'different-key'}
    assert client.post('/script-jobs',json=other).status_code==409
    run_queued(db)
    detail=client.get(f'/scripts/{selected_event}?format=json').json()
    first=detail['current'];assert first['status']=='needs_review'
    html=client.get(f'/scripts/{selected_event}').text
    assert '&lt;script&gt;' in html and '<script>window.injected' not in html
    assert '\\u003cscript\\u003e' in html
    assert client.get('/scripts').status_code==200
    assert client.get('/scripts/new').status_code==200
    edited=copy.deepcopy(first['data']);edited['hook']='Bản đã chỉnh sửa theo nguồn'
    response=client.post(f'/scripts/{selected_event}/versions',json={'base_version_id':first['id'],'data':edited})
    assert response.status_code==201,response.text
    second=response.json();assert second['data']['scenes'][0]['narration']==edited['hook']
    # Two tabs: neither a stale save nor approval may affect the newer version.
    assert client.post(f'/scripts/{selected_event}/versions',json={'base_version_id':first['id'],'data':edited}).status_code==409
    review=dict(version_id=first['id'],decision='approved',reviewer='Biên tập viên')
    assert client.post(f'/scripts/{selected_event}/reviews',json=review).status_code==409
    review['version_id']=second['id']
    assert client.post(f'/scripts/{selected_event}/reviews',json=review).status_code==201
    with db() as session:
        assert session.get(ScriptVersion,first['id']).status=='needs_review'
        assert session.get(ScriptVersion,second['id']).status=='approved'
        assert session.scalar(select(func.count()).select_from(ScriptReview))==1
        with pytest.raises(Exception):renderable_version(session,selected_event)  # fake cannot render
    assert not client.get(f'/scripts/{selected_event}?format=json').json()['render_eligible']


@pytest.mark.parametrize('status', [429,503])
def test_durable_retry_then_success(db,selected_event,monkeypatch,status):
    class Adapter(FakeProvider):
        calls=0
        def generate_script(self,data,prompt,feedback=''):
            self.calls+=1
            if self.calls==1:raise ProviderFailure('busy',status=status,retry_after=17,request_id='rid')
            return super().generate_script(data,prompt,feedback)
    adapter=Adapter('fake')
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:adapter)
    with db() as session:job_id=enqueue_script(session,request_for(selected_event)).id
    run_queued(db)
    with db() as session:
        job=session.get(ScriptJob,job_id);assert job.status=='retry_wait' and job.attempts==1
        assert (job.next_attempt_at-utcnow()).total_seconds()>15
        assert claim_script(session) is None
        job.next_attempt_at=utcnow()-timedelta(seconds=1)
    run_queued(db)
    with db() as session:
        job=session.get(ScriptJob,job_id);assert job.status=='succeeded' and job.attempts==2
        assert len(job.logs)==2 and job.logs[0]['http_status']==status


@pytest.mark.parametrize('error,attempts,kind',[(ProviderFailure('busy',status=503),6,'service_error'),
    (ProviderFailure('key',status=401),1,'configuration_error'),(ProviderFailure('timeout',uncertain=True),1,'unknown_outcome')])
def test_retry_limit_auth_and_unknown_outcome(db,selected_event,monkeypatch,error,attempts,kind):
    class Adapter(FakeProvider):
        def generate_script(self,*args):raise error
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:Adapter('fake'))
    with db() as session:job_id=enqueue_script(session,request_for(selected_event)).id
    for _ in range(attempts):
        run_queued(db)
        with db() as session:session.get(ScriptJob,job_id).next_attempt_at=utcnow()-timedelta(seconds=1)
    with db() as session:
        job=session.get(ScriptJob,job_id)
        assert job.status==('unknown_outcome' if kind=='unknown_outcome' else 'failed') and job.attempts==attempts and job.error_kind==kind
        assert claim_script(session) is None


def test_recovery_before_and_after_dispatch_stale_owner(db,selected_event):
    with db() as session:job_id=enqueue_script(session,request_for(selected_event)).id
    with db() as session:old=claim_script(session)
    with db() as session:session.get(ScriptJob,job_id).lease_until=utcnow()-timedelta(seconds=1)
    with db() as session:recover_scripts(session)
    with db() as session:new=claim_script(session)
    execute_script(*old)
    with db() as session:assert session.get(ScriptJob,job_id).attempts==0
    with db() as session:
        job=session.get(ScriptJob,job_id);job.dispatched_at=utcnow();job.attempts=1
        job.lease_until=utcnow()-timedelta(seconds=1)
    with db() as session:
        recover_scripts(session);job=session.get(ScriptJob,job_id)
        assert job.status=='unknown_outcome' and job.error_kind=='unknown_outcome'
    execute_script(*new)
    with db() as session:assert session.scalar(select(func.count()).select_from(ScriptVersion))==0


def test_insufficient_never_calls_provider(db,client,selected_event,monkeypatch):
    with db() as session:session.scalar(select(Article)).summary='Quá ngắn'
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:pytest.fail('Must not call provider'))
    with db() as session:job_id=enqueue_script(session,request_for(selected_event)).id
    run_queued(db)
    with db() as session:
        version=session.scalar(select(ScriptVersion));assert version.outcome=='insufficient_evidence' and version.status is None
        assert session.get(ScriptJob,job_id).attempts==0
        version_id=version.id
    assert client.post(f'/scripts/{selected_event}/reviews',json=dict(version_id=version_id,decision='approved',reviewer='Test')).status_code==409


def test_invalid_output_retained_and_approval_revalidates(db,client,selected_event,monkeypatch):
    class Bad(FakeProvider):
        def generate_script(self,*args):return Generation('{"wrong":true}')
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:Bad('fake'))
    with db() as session:enqueue_script(session,request_for(selected_event))
    run_queued(db)
    with db() as session:
        version=session.scalar(select(ScriptVersion));assert version.outcome=='validation_error' and version.status is None
        assert version.raw_output=='{"wrong":true}' and version.validation_errors
        version_id=version.id
    assert client.post(f'/scripts/{selected_event}/reviews',json=dict(version_id=version_id,decision='approved',reviewer='Test')).status_code==409


def test_regenerate_preserves_sources_and_approval_then_refresh(db,client,selected_event):
    with db() as session:enqueue_script(session,request_for(selected_event))
    run_queued(db)
    with db() as session:
        first=session.scalar(select(ScriptVersion));first.status='approved';first_id=first.id;snapshot_id=first.snapshot_id
        session.scalar(select(Article)).summary=TEXT+' Nguồn được cập nhật sau này.'
        enqueue_script(session,request_for(selected_event,'test-key-0002',base_version_id=first.id))
    run_queued(db)
    with db() as session:
        versions=session.scalars(select(ScriptVersion).order_by(ScriptVersion.version)).all()
        assert len(versions)==2 and versions[0].status=='approved' and versions[1].status=='needs_review'
        assert versions[1].snapshot_id==snapshot_id
        enqueue_script(session,request_for(selected_event,'test-key-0003',base_version_id=versions[1].id,refresh_sources=True))
    run_queued(db)
    with db() as session:
        last=session.scalar(select(ScriptVersion).order_by(ScriptVersion.version.desc()))
        assert last.snapshot_id!=snapshot_id
        assert session.get(ScriptVersion,first_id).status=='approved'
        snapshot=session.get(ScriptSourceSnapshot,last.snapshot_id)
        assert 'cập nhật sau này' in snapshot.payload['sources'][0]['text']
        broken=copy.deepcopy(last.data);broken['claims'][0]['evidence'][0]['quote']='Bịa'
        last.data=broken
        last_id=last.id
    assert client.post(f'/scripts/{selected_event}/reviews',json=dict(version_id=last_id,decision='approved',reviewer='Test')).status_code==422


def test_render_gate_only_latest_approved_and_selected_input(db,client,selected_event):
    from fastapi import HTTPException
    with db() as session:enqueue_script(session,request_for(selected_event))
    run_queued(db)
    with db() as session:
        version=session.scalar(select(ScriptVersion))
        # Test the handoff policy without making any real API call.
        version.provider='gemini';version_id=version.id
        with pytest.raises(HTTPException):renderable_version(session,selected_event)
        review_version(session,selected_event,version_id,'approved','Test','')
        assert renderable_version(session,selected_event).id==version_id
        edited=copy.deepcopy(version.data)
        new=save_version(session,selected_event,version_id,edited)
        assert new.status=='needs_review'
        with pytest.raises(HTTPException):renderable_version(session,selected_event)
    with db() as session:session.get(Event,selected_event).decision='pending'
    assert client.post('/script-jobs',json=request_for(selected_event,'test-key-not-selected').model_dump()).status_code==422


def test_idempotency_payload_mismatch_and_missing_key(db,client,selected_event,monkeypatch):
    payload=request_for(selected_event).model_dump()
    assert client.post('/script-jobs',json=payload).status_code==202
    assert client.post('/script-jobs',json={**payload,'target_seconds':60}).status_code==409
    run_queued(db)
    monkeypatch.setattr(get_settings(),'deepseek_api_key','')
    payload.update(idempotency_key='test-key-no-credentials',provider='deepseek')
    job_id=client.post('/script-jobs',json=payload).json()['id']
    run_queued(db)
    with db() as session:
        job=session.get(ScriptJob,job_id)
        assert job.status=='failed' and job.attempts==0 and 'API key' in job.error
