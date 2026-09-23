"""Kill a real generation process after durable dispatch; never resend its request."""
import os
import subprocess
import sys
import time
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.models import Source, Event, Article, ScriptJob, ScriptVersion, utcnow
from app.services.script_service import CreateRequest, enqueue_script, claim_script, recover_scripts
from app.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform == 'win32', reason='Linux process test')]


def until(predicate, seconds=12):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise AssertionError('Process did not reach expected durable state')


def test_killed_generation_is_unknown_and_next_request_survives(db,monkeypatch):
    monkeypatch.setattr(get_settings(),'llm_allow_fake',True)
    with db() as session:
        source=Source(name='Process fixture',url='https://example.com/process',enabled=False)
        event=Event(title='Process fixture',decision='selected')
        session.add_all([source,event]);session.flush();event_id=event.id
        session.add(Article(source_id=source.id,event_id=event.id,title='Fixture',canonical_url='https://example.com/process/story',
                            fingerprint='f'*64,summary='Dữ liệu nguồn đã được kiểm tra trong fixture. '*10))
        job_id=enqueue_script(session,CreateRequest(event_id=event_id,provider='fake',idempotency_key='process-job-first')).id
    with db() as session:claim=claim_script(session)
    code='''
import sys,time
import app.services.script_service as service
from app.llm.base import FakeProvider
class Slow(FakeProvider):
    def generate_script(self,*args):
        time.sleep(60)
        return super().generate_script(*args)
service.get_provider=lambda *args:Slow('fake')
service.execute_script(int(sys.argv[1]),sys.argv[2])
'''
    env=dict(os.environ,DATABASE_URL=os.environ['TEST_DATABASE_URL'],LLM_ALLOW_FAKE='true',POLL_SECONDS='0.1')
    process=subprocess.Popen([sys.executable,'-c',code,str(claim[0]),claim[1]],env=env)
    def dispatched():
        with db() as session:return session.get(ScriptJob,job_id).dispatched_at is not None
    try:until(dispatched)
    finally:process.kill();process.wait(timeout=5)
    with db() as session:session.get(ScriptJob,job_id).lease_until=utcnow()-timedelta(seconds=1)
    with db() as session:
        recover_scripts(session)
        job=session.get(ScriptJob,job_id)
        assert job.status=='unknown_outcome' and job.error_kind=='unknown_outcome' and job.attempts==1
        next_id=enqueue_script(session,CreateRequest(event_id=event_id,provider='fake',idempotency_key='process-job-second')).id
    worker=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def completed():
        with db() as session:return session.get(ScriptJob,next_id).status=='succeeded'
    try:
        until(completed)
        with db() as session:
            assert session.get(ScriptJob,job_id).attempts==1
            versions=session.scalars(select(ScriptVersion)).all()
            assert len(versions)==1 and versions[0].job_id==next_id
    finally:
        worker.terminate();worker.wait(timeout=10)
