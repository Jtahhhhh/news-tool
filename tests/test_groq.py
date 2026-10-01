import copy
import json
import uuid
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select, func
from app.config import get_settings
from app.llm.base import FakeProvider, Generation, ProviderFailure
from app.llm.groq_provider import GroqProvider
from app.llm.schemas import Input, Output, validate_output
from app.llm.grounding import GroundingError
from app.models import Article, Event, Source, ScriptJob, ScriptVersion, ScriptSourceSnapshot, LLMAttempt, LLMCredential, LLMPolicy, LLMQuotaState, utcnow
from app.services import llm_control as control
from app.services.script_service import enqueue_script, CreateRequest, claim_script, execute_script

FACTS = 'Bộ GD&ĐT: 91 ngành được cấp học bổng 3,7–8,4 triệu đồng/tháng. Các ngành gồm khoa học cơ bản, kỹ thuật then chốt, công nghệ chiến lược.'


def test_save_groq_policy_after_legacy_disabled_configuration(db,client):
    with db() as session:
        control.get_policy(session)
        session.get(LLMPolicy,1).data=control.Policy(routes=[control.Route(provider='deepseek',model='deepseek-test'),
            control.Route(provider='gemini',model='gemini-test')],allowed_providers=[]).model_dump()
    policy=client.get('/llm?format=json').json()['policy']
    policy.update(routes=[dict(provider='groq',model='openai/gpt-oss-120b',reservation_microusd=0)],allowed_providers=['groq'])
    response=client.post('/llm/policy',json=policy)
    assert response.status_code==200,response.text
    saved=client.get('/llm?format=json').json()['policy']
    assert saved['allowed_providers']==['groq']
    assert saved['routes'][0]['model']=='openai/gpt-oss-120b'
    with db() as session:assert control.capture_routing(session,'groq')['routes'][0]['provider']=='groq'
    html=client.get('/llm').text
    assert 'name="allow_groq" checked' in html


def article():
    return Input(schema_version='1.0',story_id='1',language='vi',target_seconds=45,tone='neutral',sources=[dict(
        source_id='article-1',url='https://example.com/scholarship',title='91 ngành được cấp học bổng 3,7–8,4 triệu đồng/tháng',published_at=None,text=FACTS)])


def draft(data=None):
    return json.loads(FakeProvider('fixture').generate_script(data or article(),'').raw)


def test_groq_request_and_parse_strict_schema():
    sent=[]
    def respond(request):
        sent.append(json.loads(request.content))
        assert request.headers['authorization']=='Bearer mock-only'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(draft())}}],
            'usage':{'prompt_tokens':20,'completion_tokens':40,'total_tokens':60}},headers={'x-request-id':'groq-mock-1'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=GroqProvider('openai/gpt-oss-120b','mock-only',client=client).generate_script(article(),'system')
    assert validate_output(article(),result.raw).decision=='draft'
    assert result.provider=='groq' and result.request_id=='groq-mock-1'
    schema=sent[0]['response_format']['json_schema']
    assert sent[0]['response_format']['type']=='json_schema' and schema['strict'] is True
    assert schema['schema']==Output.model_json_schema()
    for obj in [schema['schema'], *schema['schema']['$defs'].values()]:
        assert obj['additionalProperties'] is False
        assert set(obj['properties'])==set(obj['required'])


@pytest.mark.parametrize('status,retryable',[(429,True),(500,True),(502,True),(503,True),(504,True),(400,False),(401,False),(403,False)])
def test_groq_http_status(status,retryable):
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(status,json={'error':{'message':'mock error'}},headers={'retry-after':'2'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderFailure) as error:
            GroqProvider('openai/gpt-oss-120b','mock',client=client).generate_script(article(),'')
    assert error.value.retryable is retryable and len(calls)==1


@pytest.mark.parametrize('mutate',[
    lambda p:p.pop('caption'),
    lambda p:p['scenes'][0].update(scene_id=2),
    lambda p:p['claims'][0]['evidence'][0].update(source_id='article-999'),
    lambda p:p['claims'][0].update(text='92 ngành được cấp học bổng'),
    lambda p:p['scenes'][0].update(seconds=90),
])
def test_invalid_contract_and_figures(mutate):
    payload=draft();mutate(payload)
    with pytest.raises(ValueError):validate_output(article(),json.dumps(payload))


def test_malformed_json_and_shorter_duration():
    with pytest.raises(ValueError):validate_output(article(),'{bad')
    payload=draft();payload['scenes'][0]['seconds']=15
    assert validate_output(article(),json.dumps(payload)).scenes[0].seconds==15


@pytest.mark.parametrize('phrase',['tùy theo tiêu chí đánh giá','trong suốt thời gian học','giảm gánh nặng chi phí','nâng cao chất lượng nguồn nhân lực','sinh viên được miễn học phí','91 triệu đồng'])
@pytest.mark.parametrize('location',['claim','scene'])
def test_scholarship_unsupported_claims(phrase,location):
    payload=draft()
    if location=='claim':payload['claims'][0]['text']+=' '+phrase
    else:
        payload['scenes'][0]['narration']+=' '+phrase
        payload['hook']=payload['scenes'][0]['narration']
    with pytest.raises(GroundingError):validate_output(article(),json.dumps(payload))


@pytest.fixture
def groq_job(db,monkeypatch):
    monkeypatch.setattr(get_settings(),'llm_fetch_article',False)
    monkeypatch.setenv('GROQ_API_KEY','mock-groq-only')
    monkeypatch.setenv('GEMINI_API_KEY','mock-gemini-only')
    with db() as session:
        control.get_policy(session)
        session.get(LLMPolicy,1).data=control.Policy(routes=[control.Route(provider='groq',model='openai/gpt-oss-120b'),
            control.Route(provider='gemini',model=get_settings().gemini_model)],allowed_providers=['groq','gemini'],fallback_enabled=True).model_dump()
        source=Source(name='Scholarship fixture',url='https://example.com/feed',enabled=False)
        event=Event(title=article().sources[0].title,decision='selected')
        session.add_all([source,event]);session.flush()
        session.add(Article(source_id=source.id,event_id=event.id,title=event.title,canonical_url='https://example.com/scholarship',summary=FACTS,fingerprint=uuid.uuid4().hex))
        return enqueue_script(session,CreateRequest(event_id=event.id,provider='groq',idempotency_key=uuid.uuid4().hex)).id


def execute(db,id):
    with db() as session:
        job=session.get(ScriptJob,id);job.next_attempt_at=utcnow()-timedelta(seconds=1)
        for quota in session.scalars(select(LLMQuotaState)):quota.next_attempt_at=None
    with db() as session:claim=claim_script(session)
    assert claim and claim[0]==id
    execute_script(*claim)


@pytest.mark.parametrize('status',[429,503])
def test_three_transient_failures_then_gemini(db,groq_job,monkeypatch,status):
    calls=[]
    class Adapter(FakeProvider):
        def generate_script(self,data,prompt,feedback=''):
            calls.append(self.model)
            if len(calls)<=3:raise ProviderFailure('mock busy',status=status)
            return super().generate_script(data,prompt,feedback)
    monkeypatch.setattr('app.services.script_service.get_provider',lambda name,model,*_:Adapter(model))
    for _ in range(4):execute(db,groq_job)
    assert calls==['openai/gpt-oss-120b']*3+[get_settings().gemini_model]
    with db() as session:
        job=session.get(ScriptJob,groq_job)
        assert job.status=='succeeded' and job.route_index==1
        assert session.scalar(select(ScriptVersion)).provider=='gemini'


@pytest.mark.parametrize('repaired',[True,False])
def test_grounding_repair_once_never_fallback(db,groq_job,monkeypatch,repaired):
    calls=[]
    class Adapter(FakeProvider):
        def generate_script(self,data,prompt,feedback=''):
            calls.append((self.model,feedback))
            payload=draft(data)
            if not repaired or len(calls)==1:payload['claims'][0]['text']+=' trong suốt thời gian học'
            return Generation(json.dumps(payload),{'total_tokens':123})
    monkeypatch.setattr('app.services.script_service.get_provider',lambda name,model,*_:Adapter(model))
    execute(db,groq_job)
    with db() as session:
        assert session.scalar(select(func.count()).select_from(ScriptVersion))==0
        assert session.get(ScriptJob,groq_job).status=='retry_wait'
    execute(db,groq_job)
    with db() as session:
        job=session.get(ScriptJob,groq_job)
        assert job.repair_attempts==1 and job.attempts==2 and job.provider=='groq'
        assert job.status==('succeeded' if repaired else 'failed')
        version=session.scalar(select(ScriptVersion))
        assert version.status==('needs_review' if repaired else None)
        assert len(session.scalars(select(LLMAttempt)).all())==2
    assert len(calls)==2 and all(c[0]=='openai/gpt-oss-120b' for c in calls)
    assert 'PREVIOUS_OUTPUT_JSON' in calls[1][1]


def test_401_failfast_and_no_fallback(db,groq_job,monkeypatch):
    class Adapter(FakeProvider):
        def generate_script(self,*_):raise ProviderFailure('mock key invalid',status=401,kind='invalid_key')
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:Adapter('mock'))
    execute(db,groq_job)
    with db() as session:
        job=session.get(ScriptJob,groq_job)
        assert job.attempts==1 and job.status=='failed' and job.provider=='groq'


def test_generated_hook_is_normalized_before_repair_and_draft(db,groq_job,monkeypatch):
    class Adapter(FakeProvider):
        def generate_script(self,data,prompt,feedback=''):
            payload=draft(data);payload['hook']='Tiêu đề riêng do model tạo'
            return Generation(json.dumps(payload))
    monkeypatch.setattr('app.services.script_service.get_provider',lambda *args:Adapter('mock'))
    execute(db,groq_job)
    with db() as session:
        job=session.get(ScriptJob,groq_job);version=session.scalar(select(ScriptVersion))
        assert job.status=='succeeded' and job.attempts==1 and job.repair_attempts==0
        assert version.data['hook']==version.data['scenes'][0]['narration']
        assert json.loads(version.raw_output)['hook']=='Tiêu đề riêng do model tạo'
        assert version.status=='needs_review'


def test_article_cleaning_redirect_revalidation_and_rss_fallback(monkeypatch):
    from app.services import article_text
    monkeypatch.setattr(article_text,'validate_public_url',lambda url: url if 'example.com' in url else (_ for _ in ()).throw(ValueError('private')))
    html='<article><nav>Menu</nav><p>'+FACTS+'</p><script>bad()</script></article>'
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,text=html,headers={'content-type':'text/html'}))) as client:
        assert article_text.fetch_article('https://example.com/test',client=client)==FACTS
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(302,headers={'location':'http://127.0.0.1/private'}))) as client:
        with pytest.raises(ValueError):article_text.fetch_article('https://example.com/test',client=client)
    monkeypatch.setattr(article_text,'fetch_article',lambda url:(_ for _ in ()).throw(httpx.ReadTimeout('mock')))
    payload,details=article_text.enrich_sources(article())
    assert details['article-1']['mode']=='rss_fallback'
    assert FACTS in payload['sources'][0]['text']


def test_article_frozen_before_send(db,groq_job,monkeypatch):
    from app.services import article_text
    with db() as session:
        job=session.get(ScriptJob,groq_job);job.source_fetch_pending=True;job.prepared_requests={}
    fetched=[]
    monkeypatch.setattr(article_text,'fetch_article',lambda url:fetched.append(url) or FACTS)
    monkeypatch.setattr('app.services.script_service.get_provider',lambda name,model,*_:FakeProvider(model))
    execute(db,groq_job)
    with db() as session:
        job=session.get(ScriptJob,groq_job)
        assert job.status=='succeeded' and not job.source_fetch_pending
        assert job.source_details=={'article-1':{'mode':'full_article'}}
        assert FACTS in json.dumps(job.prepared_requests,ensure_ascii=False)
    assert len(fetched)==1


def test_env_routing_groq_to_gemini(monkeypatch):
    monkeypatch.setattr(get_settings(),'llm_provider','groq')
    monkeypatch.setattr(get_settings(),'llm_fallback_provider','gemini')
    assert [r['provider'] for r in control.default_policy()['routes']]==['groq','gemini']


def test_groq_loads_without_gemini(monkeypatch):
    import sys
    from app.llm.base import get_provider
    monkeypatch.setitem(sys.modules,'app.llm.gemini',None)
    assert isinstance(get_provider('groq','openai/gpt-oss-120b',60,8192),GroqProvider)


def test_default_backoff_bounds():
    from types import SimpleNamespace
    for number,lower,upper in [(1,1,2),(2,3,5)]:
        job=SimpleNamespace(routing={},provider='groq',logs=[{'http_status':503,'provider':'groq'}]*number)
        assert lower<=control.retry_delay(job,ProviderFailure('busy',status=503))<=upper


def test_global_concurrency_across_providers(db,groq_job,monkeypatch):
    monkeypatch.setattr(get_settings(),'llm_max_concurrency',2)
    with db() as session:
        control.seed_credentials(session)
        first=session.get(ScriptJob,groq_job)
        assert control.reserve_attempt(session,first)
        # Simulate a second in-flight reservation from another provider/process.
        session.add(LLMAttempt(job_id=groq_job,number=2,provider='gemini',model='other',reservation_until=utcnow()+timedelta(seconds=30)))
        session.flush()
        assert control.reserve_attempt(session,first) is None
        assert first.error_kind=='concurrency' and first.attempts==1


def test_groq_draft_review_edit_tts_preview_video_approve(db,groq_job,client,monkeypatch,tmp_path):
    """Mock only remote LLM/TTS; run real HTTP routes, database, FFmpeg and media checks."""
    import io
    import wave
    from app.services import video_service as video
    from app.routes import video as video_routes
    from app.models import VideoJob, VideoVersion
    calls=[]
    def groq_response(request):
        data=json.loads(json.loads(request.content)['messages'][1]['content'].split('\n',1)[1])
        payload=draft(Input.model_validate(data))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]})
    with httpx.Client(transport=httpx.MockTransport(groq_response)) as api:
        monkeypatch.setattr('app.services.script_service.get_provider',lambda name,model,*_:GroqProvider(model,client=api))
        execute(db,groq_job)
    with db() as session:
        job=session.get(ScriptJob,groq_job);assert job.status=='succeeded'
        event_id=job.event_id
        version=session.scalar(select(ScriptVersion));version_id=version.id;payload=copy.deepcopy(version.data)
    assert client.get(f'/scripts/{event_id}').status_code==200
    # Shortening uses only words and figures present in the cited source.
    payload['hook']='91 ngành được cấp học bổng 3,7–8,4 triệu đồng/tháng.'
    edited=client.post(f'/scripts/{event_id}/versions',json={'base_version_id':version_id,'data':payload})
    assert edited.status_code==201,edited.text
    with db() as session:version_id=session.scalar(select(ScriptVersion.id).order_by(ScriptVersion.id.desc()))
    reviewed=client.post(f'/scripts/{event_id}/reviews',json={'version_id':version_id,'decision':'approved','reviewer':'QA'})
    assert reviewed.status_code==201,reviewed.text
    monkeypatch.setenv('MEDIA_ROOT',str(tmp_path))
    for module in (video,video_routes):
        monkeypatch.setattr(module,'DATA',tmp_path)
        for name,folder in [('ASSETS','assets'),('AUDIO','audio'),('VIDEOS','video'),('TMP','tmp')]:
            path=tmp_path/folder;path.mkdir(exist_ok=True)
            if hasattr(module,name):monkeypatch.setattr(module,name,path)
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*(24000*3))
    def tts_response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,content=buffer.getvalue(),headers={'content-type':'audio/wav'})
    original_client=httpx.Client
    monkeypatch.setattr(video.httpx,'Client',lambda **kw:original_client(transport=httpx.MockTransport(tts_response),**kw))
    def render(preview):
        response=client.post('/video-jobs',json={'script_version_id':version_id,'idempotency_key':uuid.uuid4().hex,'preview_only':preview,'test_only':True})
        assert response.status_code==202,response.text
        with db() as session:claim=video.claim(session)
        video.execute(*claim)
        with db() as session:
            job=session.get(VideoJob,claim[0]);assert job.status=='succeeded',job.error
            return copy.deepcopy(job.checkpoint)
    checkpoint=render(True)
    audio_key=checkpoint['timeline'][0]['audio_key']
    assert client.get('/media/'+audio_key).status_code==200
    render(False)
    assert len(calls)==1 and calls[0]['input']==payload['hook']  # Render reuses validated audio.
    with db() as session:
        output=session.scalar(select(VideoVersion));output_id=output.id;output_key=output.output_key
        assert output.test_only and output.duration_seconds>0
    assert client.get('/media/'+output_key).status_code==200
    assert client.get(f'/videos/{output_id}').status_code==200
    response=client.post(f'/videos/{output_id}/reviews',json={'decision':'approved','reviewer':'QA'})
    assert response.status_code==201,response.text
    with db() as session:assert session.get(VideoVersion,output_id).status=='approved'
