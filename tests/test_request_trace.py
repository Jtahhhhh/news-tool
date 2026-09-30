import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest
from sqlalchemy import select
from app.models import Article, ScriptJob, ScriptSourceSnapshot, ScriptVersion, LLMAttempt, LLMProviderHealth, utcnow
from app.llm.base import FakeProvider, ProviderFailure, payload_hash, retry_after_seconds
from app.llm.gemini import GeminiProvider
from app.llm.schemas import Input, Output
from app.services import llm_control as control
from app.services.script_service import claim_script, execute_script, recover_scripts
from tests.test_llm_control import lab, execute, reserve, adapter, update_policy
from tests.test_scripts import input_data


def test_gemini_payload_preserves_business_schema():
    _, headers, payload = GeminiProvider('model', 'synthetic-key').build_request(input_data(), 'prompt', '')
    config = payload['generationConfig']
    assert config['responseMimeType'] == 'application/json'
    assert config['responseJsonSchema'] == Output.model_json_schema()
    assert 'responseFormat' not in config
    assert 'synthetic-key' not in json.dumps(payload)


def test_two_503_then_third_success_immutable_payload_and_one_send(db, lab, monkeypatch):
    job_id = lab['enqueue']()
    with db() as session:
        job = session.get(ScriptJob, job_id)
        frozen = job.prepared_requests.copy()
        data = Input.model_validate(session.get(ScriptSourceSnapshot, job.snapshot_id).payload)
        session.scalar(select(Article)).summary = 'Nguồn đã bị sửa sau khi xếp hàng.'
    raw = FakeProvider('mock').generate_script(data, '').raw
    sends = []
    def handler(request):
        sends.append(json.loads(request.content))
        if len(sends) <= 2:
            return httpx.Response(503, json={'error': {'status':'UNAVAILABLE','message':'temporary'}},
                headers={'retry-after':'16','x-request-id':f'r{len(sends)}','authorization':'Bearer forbidden'})
        return httpx.Response(200, json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':raw}]}}],
                                       'usageMetadata':{'totalTokenCount':22}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr('app.services.script_service.get_provider',
            lambda name,model,timeout,limit: GeminiProvider(model,timeout=timeout,output_limit=limit,client=client))
        for i in range(3):
            with db() as session:
                h=session.scalar(select(LLMProviderHealth))
                if h and h.state=='open':h.next_attempt_at=utcnow()-timedelta(seconds=1)
            execute(db,job_id)
            with db() as session:
                job=session.get(ScriptJob,job_id)
                if i<2:
                    assert job.status=='retry_wait'
                    delay=(job.next_attempt_at-utcnow()).total_seconds()
                    assert 15<=delay<=17  # Retry-After overrides shorter jitter.
        assert len(sends)==3
        assert len({payload_hash(p) for p in sends})==1
    with db() as session:
        job=session.get(ScriptJob,job_id)
        assert job.status=='succeeded' and job.attempts==3 and job.prepared_requests==frozen
        assert session.scalar(select(ScriptVersion)).status=='needs_review'
        attempts=session.scalars(select(LLMAttempt).order_by(LLMAttempt.number)).all()
        assert len({a.trace['payload_hash'] for a in attempts})==1
        assert all(a.trace['schema_hash'] and a.trace['snapshot_hash'] and a.trace['prompt_hash'] for a in attempts)
        assert attempts[0].trace['provider_code']=='UNAVAILABLE'
        assert attempts[0].trace['headers']['retry-after']=='16'
        assert 'authorization' not in attempts[0].trace['headers']
        assert not attempts[0].trace['usage_available']


def test_retry_after_beyond_deadline_is_terminal_without_early_resend(db,lab,monkeypatch):
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503,retry_after=172800)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        job=session.get(ScriptJob,job_id)
        assert job.status=='failed' and job.attempts==1
        assert (job.next_attempt_at-utcnow()).total_seconds()>172798
        assert claim_script(session) is None
    assert len(calls)==1


def test_http_date_retry_after_and_content_errors_are_distinct():
    assert 89<=retry_after_seconds(format_datetime(utcnow()+timedelta(seconds=90),usegmt=True))<=90
    assert retry_after_seconds('Wed, 01 Jan 2020 00:00:00 GMT')==0
    for body,kind in [({},'empty_response'),({'candidates':[{'finishReason':'MAX_TOKENS'}]},'output_truncated'),
                      ({'candidates':[{'finishReason':'SAFETY'}]},'content_refusal')]:
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body))) as client:
            with pytest.raises(ProviderFailure) as error:
                GeminiProvider('model','synthetic-key',client=client).generate_script(input_data(),'prompt')
            assert error.value.kind==kind and not error.value.retryable


def test_concurrency_shared_keys_default_one_and_configurable(db,lab):
    ids=[lab['enqueue'](),lab['enqueue']()]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda i:reserve(db,i),ids))
    assert sum(r is not None for r in results)==1
    with db() as session:
        attempts=session.scalars(select(LLMAttempt)).all();assert len(attempts)==1
        attempts[0].status='unknown_outcome';attempts[0].finished_at=utcnow()
    waiting=ids[results.index(None)]
    assert reserve(db,waiting) is None  # cancelled/unknown sends still occupy the slot
    with db() as session:
        session.scalar(select(LLMAttempt)).reservation_until=utcnow()-timedelta(seconds=1)
    assert reserve(db,waiting) is not None


def test_retry_schedule_survives_new_connection_and_recovery(db,lab,monkeypatch):
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)])
    job_id=lab['enqueue']();execute(db,job_id)
    with db() as session:
        job=session.get(ScriptJob,job_id);when=job.next_attempt_at;frozen=job.prepared_requests
    from app.database import engine
    engine.dispose()
    with db() as session:
        recover_scripts(session)
        job=session.get(ScriptJob,job_id)
        assert job.status=='retry_wait' and job.next_attempt_at==when and job.prepared_requests==frozen
        assert claim_script(session) is None
    assert len(calls)==1


def test_expired_retry_never_sends_and_active_manual_retry_rejected(db,lab,monkeypatch,client):
    calls=adapter(monkeypatch,[ProviderFailure('busy',status=503)])
    job_id=lab['enqueue']();execute(db,job_id)
    assert client.post(f'/llm/jobs/{job_id}/retry',json={}).status_code==409
    with db() as session:session.get(ScriptJob,job_id).retry_started_at=utcnow()-timedelta(seconds=901)
    execute(db,job_id)
    with db() as session:
        job=session.get(ScriptJob,job_id);assert job.status=='failed' and job.error_kind=='retry_deadline'
    assert len(calls)==1
