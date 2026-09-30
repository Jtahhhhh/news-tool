import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit, parse_qs
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select, func
from app.models import (Event, ScriptSourceSnapshot, ScriptVersion, VideoVersion, VideoReview,
                        TikTokAccount, TikTokOAuthState, PublishJob, PublishAttempt, utcnow)
from app.services.media_integrity import seal_review
from app.services.publishing import CreatePublish, enqueue, eligible, cancel, reconcile, verified_copy
from app.tiktok import config
from app.tiktok.api import TikTokAPI, APIError, chunks, upload_url
from app.tiktok.accounts import begin_oauth,consume_state,digest,access_token,apply_tokens,disconnect
from app.publish_worker import run_once, job_lock, claim, save_status


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv('TIKTOK_ENABLED','true')
    monkeypatch.setenv('TIKTOK_CLIENT_KEY','fake-client')
    monkeypatch.setenv('TIKTOK_CLIENT_SECRET','secret-not-for-output')
    monkeypatch.setenv('TIKTOK_TOKEN_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv('TIKTOK_REDIRECT_URI','https://news.example.test/tiktok/callback')


@pytest.fixture
def ready(db,settings,tmp_path,monkeypatch):
    monkeypatch.setenv('MEDIA_ROOT',str(tmp_path))
    folder=tmp_path/'video';folder.mkdir()
    path=folder/'reviewed.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=s=360x640:r=25:d=3',
                    '-c:v','libx264',str(path)],check=True)
    with db() as s:
        event=Event(title='Test story',decision='selected');s.add(event);s.flush()
        snapshot=ScriptSourceSnapshot(event_id=event.id,payload={},digest='a'*64);s.add(snapshot);s.flush()
        script=ScriptVersion(event_id=event.id,version=1,snapshot_id=snapshot.id,provider='gemini',model='test',
            outcome='draft',status='approved',data={},raw_output='',prompt_version='1',schema_version='1',prompt_hash='a'*64,schema_hash='b'*64)
        s.add(script);s.flush()
        v=VideoVersion(script_version_id=script.id,version=1,status='approved',config={},timeline={'scenes':[]},
            input_hash='c'*64,output_key='video/reviewed.mp4',probe={},duration_seconds=3,test_only=False)
        s.add(v);s.flush();checksum=seal_review(v)
        s.add(VideoReview(video_version_id=v.id,decision='approved',reviewer='QA',output_sha256=checksum))
        account=TikTokAccount(open_id='open-test-user',display_name='QA creator')
        apply_tokens(account,tokens());s.add(account);s.flush()
        return dict(video_version_id=v.id,account_id=account.id,video_sha256=checksum,
                    idempotency_key='publish-test-001',caption='Caption #test',confirmed=True),path


def tokens():
    return dict(open_id='open-test-user',scope='user.info.basic,video.upload',access_token='secret-access-token',
                refresh_token='secret-refresh-token',expires_in=3600,refresh_expires_in=86400,token_type='Bearer')


class MockAPI:
    def __init__(self):self.calls=[];self.remote='SEND_TO_USER_INBOX';self.offset=0
    def initialize(self,token,size):
        self.calls.append('init');return {'publish_id':'pid-1','upload_url':'https://open-upload.tiktokapis.com/video/?token=secret-upload'}
    def transfer(self,url,data,offset,size):self.calls.append('chunk');self.offset=offset+len(data)
    def status(self,token,pid):
        self.calls.append('status');return {'status':self.remote,'uploaded_bytes':self.offset,'publicaly_available_post_id':['123']}
    def refresh(self,token):self.calls.append('refresh');return tokens()
    def revoke(self,token):self.calls.append('revoke');return {}


def queued(db,ready):
    with db() as s:return enqueue(s,CreatePublish(**ready[0])).id


def due(db,id):
    with db() as s:s.get(PublishJob,id).next_run_at=utcnow()-timedelta(seconds=1)


@pytest.mark.parametrize('size',[1,4_000_000,5_000_000,32_000_000,64_000_001,4_000_000_000])
def test_chunks(size):
    chunk,count=chunks(size)
    assert count==size//chunk and 1<=count<=1000
    last=size-(count-1)*chunk
    assert last<=128_000_000
    if count>1:assert 5_000_000<=chunk<=64_000_000


@pytest.mark.parametrize('url',['http://open-upload.tiktokapis.com/a','https://evil.test/a',
    'https://open-upload.tiktokapis.com.evil.test/a','https://u:p@open-upload.tiktokapis.com/a'])
def test_upload_url_rejects_untrusted(url):
    with pytest.raises(APIError):upload_url(url)


def test_adapter_contract_and_redaction(settings):
    requests=[]
    def handler(request):
        requests.append(request)
        if request.method=='PUT':return httpx.Response(201)
        return httpx.Response(200,json={'data':{'publish_id':'pid','upload_url':'https://open-upload.tiktokapis.com/video/?token=secret'},'error':{'code':'ok'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api=TikTokAPI(client);api.initialize('secret',123)
        assert json.loads(requests[0].content)=={'source_info':{'source':'FILE_UPLOAD','video_size':123,'chunk_size':123,'total_chunk_count':1}}
        api.transfer('https://open-upload.tiktokapis.com/video/?token=secret',b'abc',0,3)
        assert requests[1].headers['content-range']=='bytes 0-2/3'
        assert 'authorization' not in requests[1].headers
    for status,body in [(401,{'error':{'code':'access_token_invalid','message':'secret-access-token'}}),(500,{'error':{'code':'internal_error'}})]:
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body))) as client:
            with pytest.raises(APIError) as error:TikTokAPI(client).status('secret','pid')
            assert 'secret' not in str(error.value)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='secret-garbage'))) as client:
        with pytest.raises(APIError) as error:TikTokAPI(client).initialize('secret',10)
        assert error.value.uncertain


def test_oauth_state_binding_expiry_and_replay(db,settings):
    url,browser=begin_oauth();state=parse_qs(urlsplit(url).query)['state'][0]
    with pytest.raises(HTTPException):consume_state(state,'wrong-browser')
    consume_state(state,browser)
    with pytest.raises(HTTPException):consume_state(state,browser)
    url,browser=begin_oauth();state=parse_qs(urlsplit(url).query)['state'][0]
    with db() as s:s.get(TikTokOAuthState,digest(state)).expires_at=utcnow()-timedelta(seconds=1)
    with pytest.raises(HTTPException):consume_state(state,browser)


@pytest.mark.parametrize('bad',['unapproved','test','fake','review_missing','hash_changed','file_missing','review_hash'])
def test_gate_rejects_bypass(db,client,ready,bad):
    payload,path=ready
    with db() as s:
        v=s.get(VideoVersion,payload['video_version_id'])
        if bad=='unapproved':v.status='needs_review'
        if bad=='test':v.test_only=True
        if bad=='fake':s.get(ScriptVersion,v.script_version_id).provider='fake'
        if bad=='review_missing':s.delete(s.scalar(select(VideoReview)))
        if bad=='review_hash':s.scalar(select(VideoReview)).output_sha256=None
    if bad=='hash_changed':path.write_bytes(b'tampered')
    if bad=='file_missing':path.unlink()
    assert client.post('/publish-jobs',json=payload).status_code==409


def test_consent_idempotency_and_concurrent_requests(db,client,ready):
    payload,_=ready
    assert client.post('/publish-jobs',json={**payload,'confirmed':False}).status_code==422
    assert client.post('/publish-jobs',json={**payload,'mode':'direct'}).status_code==422
    def submit():
        with db() as s:return enqueue(s,CreatePublish(**payload)).id
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:submit(),range(2)))
    assert ids[0]==ids[1]
    assert client.post('/publish-jobs',json={**payload,'idempotency_key':'other-key-001'}).status_code==409
    assert client.post('/publish-jobs',json={**payload,'caption':'different'}).status_code==409


def test_complete_upload_requires_api_post_evidence(db,client,ready):
    id=queued(db,ready);api=MockAPI()
    for _ in range(3):due(db,id);run_once(api)
    result=client.get(f'/publish-jobs/{id}').json()
    assert result['status']=='inbox' and 'cần hoàn tất đăng' in result['label']
    assert api.calls==['init','chunk','status']
    assert all(secret not in client.get('/publishing').text+json.dumps(result) for secret in ('secret-access-token','secret-refresh-token','secret-upload','access_cipher','upload_cipher'))
    api.remote='PUBLISH_COMPLETE';due(db,id);run_once(api)
    assert client.get(f'/publish-jobs/{id}').json()['status']=='published'


def test_mutation_between_enqueue_and_send_never_calls_api(db,ready):
    id=queued(db,ready);ready[1].write_bytes(b'changed');api=MockAPI();run_once(api)
    assert not api.calls
    with db() as s:assert s.get(PublishJob,id).status=='failed'


def test_timeout_init_no_duplicate_after_restart(db,ready):
    id=queued(db,ready)
    class Timeout(MockAPI):
        def initialize(self,*a):self.calls.append('init');raise APIError('network_error',uncertain=True)
    api=Timeout();run_once(api);due(db,id);run_once(api)
    assert api.calls==['init']
    with db() as s:
        job=s.get(PublishJob,id);assert job.status=='unknown_outcome'
        with pytest.raises(HTTPException):reconcile(s,job)


def test_crash_after_init_intent_never_reinitializes(db,ready):
    id=queued(db,ready)
    with db() as s:
        job=s.get(PublishJob,id);job.status='initializing';job.init_attempts=1
        job.owner='dead';job.lease_until=utcnow()-timedelta(seconds=1)
    api=MockAPI();run_once(api)
    with db() as s:assert s.get(PublishJob,id).status=='unknown_outcome'
    assert api.calls==[]


def test_upload_timeout_reconciles_existing_id(db,ready):
    id=queued(db,ready)
    class Timeout(MockAPI):
        def transfer(self,url,data,offset,size):
            self.calls.append('chunk');self.offset=size;raise APIError('network_error',uncertain=True)
    api=Timeout();run_once(api);due(db,id);run_once(api)
    api.remote='PROCESSING_UPLOAD';due(db,id);run_once(api)
    assert api.calls==['init','chunk','status']
    with db() as s:assert s.get(PublishJob,id).status=='polling'


def test_two_workers_and_expired_lease_cannot_duplicate_live_request(db,ready):
    id=queued(db,ready);entered=threading.Event();release=threading.Event()
    class Slow(MockAPI):
        def initialize(self,*a):entered.set();assert release.wait(10);return super().initialize(*a)
    api=Slow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(run_once,api);assert entered.wait(10)
        with db() as s:s.get(PublishJob,id).lease_until=utcnow()-timedelta(seconds=1)
        assert pool.submit(run_once,api).result(timeout=5) is False
        # Restore lease to allow the live owner to commit; no second sender ran.
        with db() as s:s.get(PublishJob,id).lease_until=utcnow()+timedelta(seconds=180)
        release.set();first.result(timeout=10)
    assert api.calls==['init']


def test_cancel_during_init_preserves_publish_id_without_resuming(db,ready):
    id=queued(db,ready)
    class Cancel(MockAPI):
        def initialize(self,*a):
            with db() as s:cancel(s,s.get(PublishJob,id))
            return super().initialize(*a)
    api=Cancel();run_once(api)
    with db() as s:
        job=s.get(PublishJob,id);assert job.status=='stopped' and job.publish_id=='pid-1'
        reconcile(s,job)
    api.remote='PROCESSING_UPLOAD';run_once(api)
    assert api.calls==['init','status']
    with db() as s:assert s.get(PublishJob,id).status=='polling'


def test_refresh_serialized_and_disconnect(db,ready):
    account_id=ready[0]['account_id'];api=MockAPI()
    with db() as s:s.get(TikTokAccount,account_id).access_expires=utcnow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:access_token(account_id,api),range(2)))
    assert values==['secret-access-token']*2 and api.calls==['refresh']
    with db() as s:
        account=s.get(TikTokAccount,account_id)
        assert 'secret-access-token' not in account.access_cipher
    disconnect(account_id,api)
    with pytest.raises(APIError):access_token(account_id,api)
    with db() as s:assert not s.get(TikTokAccount,account_id).access_cipher


def test_refresh_failure_requires_reconnect(db,ready):
    account_id=ready[0]['account_id']
    with db() as s:s.get(TikTokAccount,account_id).access_expires=utcnow()
    class Bad(MockAPI):
        def refresh(self,*a):raise APIError('network_error',uncertain=True)
    with pytest.raises(APIError):access_token(account_id,Bad())
    with db() as s:assert s.get(TikTokAccount,account_id).state=='reconnect_required'


def test_api_limit_rejection_retries_with_backoff(db,ready):
    id=queued(db,ready)
    class Rate(MockAPI):
        def initialize(self,*a):raise APIError('rate_limit_exceeded',429,retry_after=180)
    run_once(Rate())
    with db() as s:
        job=s.get(PublishJob,id)
        assert job.status=='queued' and job.next_run_at>utcnow()+timedelta(seconds=170)


def test_review_reprobes_and_new_render_is_not_approved(db,client,ready):
    payload,path=ready
    with db() as s:
        v=s.get(VideoVersion,payload['video_version_id']);v.status='needs_review'
    path.write_bytes(b'bad-video')
    assert client.post(f"/videos/{payload['video_version_id']}/reviews",json={'decision':'approved','reviewer':'QA'}).status_code==409


def test_oauth_callback_consumed_even_when_provider_fails(db,client,settings,monkeypatch):
    url,browser=begin_oauth();state=parse_qs(urlsplit(url).query)['state'][0]
    client.cookies.set('tiktok_oauth_browser',browser)
    def fail(*args):raise APIError('network_error',uncertain=True)
    monkeypatch.setattr(TikTokAPI,'exchange',fail)
    response=client.get('/tiktok/callback',params={'state':state,'code':'secret-code'})
    assert response.status_code==502 and 'secret-code' not in response.text
    assert response.headers['referrer-policy']=='no-referrer'
    assert client.get('/tiktok/callback',params={'state':state,'code':'secret-code'}).status_code==400


def test_rechecks_review_and_bytes_before_transfer(db,ready):
    id=queued(db,ready);api=MockAPI();run_once(api)
    ready[1].write_bytes(b'changed-after-init')
    due(db,id);run_once(api)
    assert api.calls==['init']
    with db() as s:
        job=s.get(PublishJob,id)
        assert job.publish_id=='pid-1' and job.status=='unknown_outcome'


def test_bounded_transfer_retries(db,ready):
    id=queued(db,ready);api=MockAPI();run_once(api)
    with db() as s:
        for _ in range(6):s.add(PublishAttempt(job_id=id,operation='chunk',outcome='network_error'))
    due(db,id);run_once(api)
    assert api.calls==['init']
    with db() as s:assert s.get(PublishJob,id).error=='transfer_budget_exhausted'


@pytest.mark.parametrize('offset',[None,-1,1,True])
def test_unconfirmed_offset_never_resumes(db,ready,offset):
    id=queued(db,ready);api=MockAPI();run_once(api)
    with db() as s:
        job=s.get(PublishJob,id);job.status='reconcile'
        save_status(job,{'status':'PROCESSING_UPLOAD','uploaded_bytes':offset})
        assert job.status=='unknown_outcome' and job.next_run_at is None


def test_crash_during_put_reconciles_before_continuing(db,ready):
    id=queued(db,ready);api=MockAPI();run_once(api)
    with db() as s:
        job=s.get(PublishJob,id);job.status='transferring'
        job.owner='dead';job.lease_until=utcnow()-timedelta(seconds=1)
    api.remote='PROCESSING_UPLOAD';api.offset=0
    due(db,id);run_once(api)
    assert api.calls==['init','status']
    due(db,id);run_once(api)
    assert api.calls==['init','status','chunk']


def test_revoke_failure_blocks_local_sending(db,ready):
    account_id=ready[0]['account_id']
    class Bad(MockAPI):
        def revoke(self,*a):raise APIError('network_error',uncertain=True)
    with pytest.raises(HTTPException):disconnect(account_id,Bad())
    with db() as s:assert s.get(TikTokAccount,account_id).state=='revoke_unconfirmed'
    with pytest.raises(APIError):access_token(account_id,MockAPI())


def test_remote_failed_and_status_retry_limit(db,ready):
    id=queued(db,ready);api=MockAPI();run_once(api)
    with db() as s:
        job=s.get(PublishJob,id);job.status='polling'
    class Bad(MockAPI):
        def status(self,*a):raise APIError('internal_error',500,uncertain=True)
    for _ in range(5):due(db,id);run_once(Bad())
    with db() as s:
        job=s.get(PublishJob,id);assert job.status=='unknown_outcome'
        reconcile(s,job)
    api.remote='FAILED';due(db,id);run_once(api)
    with db() as s:assert s.get(PublishJob,id).status=='failed'
    with db() as s:
        job=s.get(PublishJob,id)
        save_status(job,{'status':'FAILED','fail_reason':'duration_check_failed'})
        assert job.error=='duration_check_failed'
        save_status(job,{'status':'FAILED','fail_reason':'secret-value-in-response'})
        assert job.error=='tiktok_processing_failed'


def test_oauth_success_and_secure_cookie(db,client,settings,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(TikTokAPI,'exchange',lambda self,code:tokens())
    monkeypatch.setattr(TikTokAPI,'profile',lambda self,token:{'open_id':'open-test-user','display_name':'Creator'})
    with TestClient(app,base_url='https://news.example.test') as browser:
        browser.get('/publishing')
        response=browser.post('/tiktok/connect',headers={'x-csrf-token':browser.cookies['csrf_token']},follow_redirects=False)
        assert response.status_code==303
        assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
        state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        response=browser.get('/tiktok/callback',params={'state':state,'code':'fake-code'},follow_redirects=False)
        assert response.status_code==303 and response.headers['location']=='/publishing'
    with db() as s:
        account=s.scalar(select(TikTokAccount))
        assert account.display_name=='Creator' and account.state=='connected'


def test_new_version_does_not_inherit_publish_approval(db,client,ready):
    payload,_=ready
    with db() as s:
        old=s.get(VideoVersion,payload['video_version_id'])
        new=VideoVersion(script_version_id=old.script_version_id,version=2,parent_version_id=old.id,
            config={},timeline={},input_hash='d'*64,output_key='video/new.mp4',duration_seconds=3,test_only=False)
        s.add(new);s.flush();new_id=new.id
    assert client.post('/publish-jobs',json={**payload,'video_version_id':new_id}).status_code==409


def test_token_wrong_encryption_key_is_not_plaintext_fallback(settings,monkeypatch):
    value=config.encrypt('secret-access-token')
    monkeypatch.setenv('TIKTOK_TOKEN_KEY',Fernet.generate_key().decode())
    with pytest.raises(RuntimeError):config.decrypt(value)


def test_api_errors_do_not_echo_unexpected_provider_values(settings):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(500,json={'error':['secret-value']}))) as client:
        with pytest.raises(APIError) as error:TikTokAPI(client).initialize('secret',100)
        assert str(error.value)=='provider_error'
