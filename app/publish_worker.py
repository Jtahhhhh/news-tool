"""One bounded API operation per claim; durable intent precedes every send."""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import signal
import sys
import threading
import time
import uuid
from sqlalchemy import select, text, update, func
from app.database import engine, session_scope
from app.models import PublishJob, PublishAttempt, TikTokAccount, utcnow
from app.services.publishing import verified_copy
from app.tiktok import config
from app.tiktok.accounts import access_token
from app.tiktok.api import TikTokAPI, APIError, chunks, upload_url

HEARTBEAT=Path('/tmp/news-tool-publish-heartbeat')
RUNNABLE=('queued','initializing','uploading','transferring','reconcile','polling','inbox')
FAIL_REASONS={'file_format_check_failed','duration_check_failed','frame_rate_check_failed',
              'picture_size_check_failed','internal','video_pull_failed','photo_pull_failed',
              'publish_cancelled','auth_removed','spam_risk_too_many_posts',
              'spam_risk_user_banned_from_posting','spam_risk_text','spam_risk'}
stopping=False


@contextmanager
def job_lock(job_id):
    # All claimants acquire this session lock. Even an expired lease cannot steal
    # work while a live HTTP request retains its DB connection.
    with engine.connect() as connection:
        acquired=connection.scalar(text('SELECT pg_try_advisory_lock(721006,:id)'),{'id':job_id})
        connection.commit()
        try: yield acquired
        finally:
            if acquired:
                connection.execute(text('SELECT pg_advisory_unlock(721006,:id)'),{'id':job_id})
                connection.commit()


def claim(job_id):
    with session_scope() as s:
        job=s.scalar(select(PublishJob).where(PublishJob.id==job_id).with_for_update())
        now=utcnow()
        if (not job or job.status not in RUNNABLE or not job.next_run_at or job.next_run_at>now
                or (job.owner and job.lease_until and job.lease_until>now)):
            return None
        if job.status=='initializing':
            job.status='reconcile' if job.publish_id else 'unknown_outcome'
            if not job.publish_id:
                job.error='init_interrupted';job.next_run_at=None;job.owner=None;job.lease_until=None
                return None
        if job.status=='transferring': job.status='reconcile'
        job.owner=uuid.uuid4().hex;job.lease_until=now+timedelta(seconds=180)
        return job.owner


def owned(s,job_id,owner):
    job=s.scalar(select(PublishJob).where(PublishJob.id==job_id).with_for_update())
    if not job or job.owner!=owner or not job.lease_until or job.lease_until<=utcnow():
        raise LostOwnership()
    return job


class LostOwnership(Exception): pass


def pulse(job_id,owner,done):
    while not done.wait(10):
        HEARTBEAT.touch()
        try:
            with session_scope() as s:
                s.execute(update(PublishJob).where(PublishJob.id==job_id,PublishJob.owner==owner)
                          .values(lease_until=utcnow()+timedelta(seconds=180)))
        except Exception:
            # Main operation is fenced at the next durable transition.
            return


def dispatch(job_id,owner,operation):
    with session_scope() as s:
        job=owned(s,job_id,owner)
        account=s.get(TikTokAccount,job.account_id)
        if not account or account.state!='connected' or account.open_id!=job.snapshot['account_open_id']:
            raise APIError('access_token_invalid')
        s.execute(text('SELECT pg_advisory_xact_lock(721010)'))
        limit=6 if operation=='init' else 30
        if operation=='chunk':
            sent=s.scalar(select(func.count()).select_from(PublishAttempt).where(
                PublishAttempt.job_id==job.id,PublishAttempt.operation=='chunk'))
            if sent>=chunks(job.snapshot['size'])[1]+5:
                job.status='unknown_outcome';job.next_run_at=None;job.error='transfer_budget_exhausted'
                return None
        if operation!='chunk':
            recent=s.scalar(select(func.count()).select_from(PublishAttempt).join(PublishJob)
                .where(PublishJob.account_id==job.account_id,PublishAttempt.operation==operation,
                       PublishAttempt.created_at>utcnow()-timedelta(seconds=61)))
            if recent>=limit:
                job.next_run_at=utcnow()+timedelta(seconds=61)
                return None
        if operation=='init':
            if job.publish_id or job.init_attempts>=3: raise APIError('invalid_request')
            job.status='initializing';job.init_attempts+=1
        if operation=='chunk':job.status='transferring'
        attempt=PublishAttempt(job_id=job.id,operation=operation)
        s.add(attempt);s.flush();return attempt.id


def finish_attempt(s,attempt_id,outcome,http_status=None):
    if attempt_id:
        attempt=s.get(PublishAttempt,attempt_id)
        attempt.outcome=outcome;attempt.http_status=http_status;attempt.finished_at=utcnow()


def save_status(job,data):
    remote=data.get('status')
    if remote not in ('PROCESSING_UPLOAD','PROCESSING_DOWNLOAD','SEND_TO_USER_INBOX','PUBLISH_COMPLETE','FAILED'):
        raise APIError('invalid_response',uncertain=True)
    previous=job.status
    job.remote_status=remote;job.error=None;job.failures=0;job.poll_count+=1
    job.next_run_at=utcnow()+timedelta(seconds=30)
    if remote=='PUBLISH_COMPLETE':
        job.status='published';job.next_run_at=None;job.upload_cipher=None
        ids=data.get('publicaly_available_post_id',[])
        job.post_ids=[str(v) for v in ids if str(v).isdigit()][:20] if isinstance(ids,list) else []
    elif remote=='SEND_TO_USER_INBOX':
        job.status='inbox';job.next_run_at=utcnow()+timedelta(minutes=5);job.upload_cipher=None
    elif remote=='FAILED':
        reason=data.get('fail_reason')
        job.status='failed';job.error=reason if isinstance(reason,str) and reason in FAIL_REASONS else 'tiktok_processing_failed'
        job.next_run_at=None;job.upload_cipher=None
    elif remote=='PROCESSING_UPLOAD' and previous=='reconcile':
        size=job.snapshot['size'];offset=data.get('uploaded_bytes')
        chunk,count=chunks(size)
        boundaries={i*chunk for i in range(count)}|{size}
        # Only a server-confirmed complete chunk boundary can resume a PUT.
        if (not isinstance(offset,int) or isinstance(offset,bool) or offset not in boundaries
                or offset<job.sent_bytes):
            job.status='unknown_outcome';job.error='upload_offset_unconfirmed';job.next_run_at=None
        elif offset==size:
            job.sent_bytes=size;job.status='polling'
        elif not job.upload_cipher or not job.upload_expires or job.upload_expires<=utcnow():
            job.status='unknown_outcome';job.error='upload_url_expired';job.next_run_at=None
        else:
            job.sent_bytes=offset;job.status='uploading';job.next_run_at=utcnow()
    else:
        job.status='polling'
    if job.poll_count>=288 and job.status not in ('failed','published','unknown_outcome'):
        # Bound automatic polling; manual reconciliation never creates an upload.
        job.next_run_at=None
        job.error='polling_paused_check_manually'


def handle_error(job_id,owner,operation,attempt_id,error):
    with session_scope() as s:
        job=owned(s,job_id,owner)
        finish_attempt(s,attempt_id,error.code,error.status)
        job.failures+=1;job.error=error.code
        wait=max(min(300,15*2**min(job.failures-1,5)),error.retry_after)
        job.next_run_at=utcnow()+timedelta(seconds=wait)
        if error.code in ('access_token_invalid','scope_not_authorized'):
            account=s.get(TikTokAccount,job.account_id)
            if account and account.state=='connected':account.state='reconnect_required'
            job.status='unknown_outcome' if job.publish_id else 'failed';job.next_run_at=None
        elif operation=='init':
            # Only an explicit 429 rejection can be safely initialized again.
            if error.status==429 and error.code=='rate_limit_exceeded' and job.init_attempts<3:
                job.status='queued'
            else:
                job.status='unknown_outcome' if error.uncertain or job.publish_id else 'failed'
                job.next_run_at=None
        elif operation=='chunk':
            job.status='reconcile'
            if job.failures>=5:job.status='unknown_outcome';job.next_run_at=None
        elif job.failures>=5 or (error.status and 400<=error.status<500 and error.status!=429):
            job.status='unknown_outcome';job.next_run_at=None
        else:job.status='reconcile' if job.status=='reconcile' else 'polling'


def step(job_id,owner,api):
    operation='status';attempt_id=None
    try:
        with session_scope() as s:
            job=owned(s,job_id,owner)
            account_id=job.account_id;stage=job.status
            publish_id=job.publish_id
        operation='init' if stage=='queued' else 'chunk' if stage=='uploading' else 'status'
        token=access_token(account_id,api)
        if operation=='init':
            with session_scope() as s:
                job=owned(s,job_id,owner)
                with verified_copy(s,job) as path:size=path.stat().st_size
            attempt_id=dispatch(job_id,owner,'init')
            if attempt_id is None:return
            result=api.initialize(token,size)
            # Persist identity even if upload URL processing later fails.
            with session_scope() as s:
                job=owned(s,job_id,owner);job.publish_id=result['publish_id']
            url=config.encrypt(upload_url(result.get('upload_url')))
            with session_scope() as s:
                job=owned(s,job_id,owner);job.upload_cipher=url
                job.upload_expires=utcnow()+timedelta(minutes=59)
                job.status='uploading';job.next_run_at=utcnow();job.error=None;job.failures=0
                finish_attempt(s,attempt_id,'ok',200)
        elif operation=='chunk':
            with session_scope() as s:
                job=owned(s,job_id,owner)
                if not job.upload_expires or job.upload_expires<=utcnow():
                    job.status='reconcile';job.next_run_at=utcnow();return
                offset,size,url=job.sent_bytes,job.snapshot['size'],config.decrypt(job.upload_cipher)
                chunk,count=chunks(size)
                length=size-offset if offset//chunk==count-1 else chunk
                with verified_copy(s,job) as path:
                    with path.open('rb') as f:f.seek(offset);data=f.read(length)
            attempt_id=dispatch(job_id,owner,'chunk')
            if attempt_id is None:return
            api.transfer(url,data,offset,size)
            with session_scope() as s:
                job=owned(s,job_id,owner);job.sent_bytes=offset+len(data)
                job.status='polling' if job.sent_bytes==size else 'uploading'
                job.next_run_at=utcnow()+timedelta(seconds=3);job.failures=0;job.error=None
                finish_attempt(s,attempt_id,'ok',201 if job.sent_bytes==size else 206)
        else:
            if not publish_id:raise APIError('invalid_publish_id')
            attempt_id=dispatch(job_id,owner,'status')
            if attempt_id is None:return
            result=api.status(token,publish_id)
            with session_scope() as s:
                job=owned(s,job_id,owner);save_status(job,result);finish_attempt(s,attempt_id,'ok',200)
    except LostOwnership:
        # A local cancellation may have happened while the HTTP request was in
        # flight. Never overwrite that decision; preserve remote identity below.
        if operation=='init' and 'result' in locals() and result.get('publish_id'):
            with session_scope() as s:
                job=s.get(PublishJob,job_id)
                if job and not job.publish_id:job.publish_id=result['publish_id']
        with session_scope() as s:finish_attempt(s,attempt_id,'ownership_lost')
    except APIError as error:
        try:handle_error(job_id,owner,operation,attempt_id,error)
        except LostOwnership:pass
    except Exception:
        # Never persist str(exc): HTTP exceptions may embed bearer/signed URLs.
        try:
            with session_scope() as s:
                job=owned(s,job_id,owner)
                job.status='unknown_outcome' if job.init_attempts or job.publish_id else 'failed'
                job.error='local_validation_or_configuration_error';job.next_run_at=None
                finish_attempt(s,attempt_id,'local_error')
        except LostOwnership:pass


def run_once(api=None):
    if not config.enabled():return False
    with session_scope() as s:
        ids=list(s.scalars(select(PublishJob.id).where(PublishJob.status.in_(RUNNABLE),
            PublishJob.next_run_at<=utcnow()).order_by(PublishJob.next_run_at,PublishJob.id).limit(20)))
    for job_id in ids:
        with job_lock(job_id) as acquired:
            if not acquired:continue
            owner=claim(job_id)
            if not owner:continue
            done=threading.Event();thread=threading.Thread(target=pulse,args=(job_id,owner,done),daemon=True);thread.start()
            try:
                if api:step(job_id,owner,api)
                else:
                    with TikTokAPI() as client:step(job_id,owner,client)
            finally:
                done.set();thread.join(timeout=2)
                with session_scope() as s:
                    s.execute(update(PublishJob).where(PublishJob.id==job_id,PublishJob.owner==owner)
                        .values(owner=None,lease_until=None))
            return True
    return False


def stop(*_):
    global stopping
    stopping=True


def main():
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    if config.enabled():config.require_config()
    HEARTBEAT.touch()
    while not stopping:
        try:
            worked=run_once()
            HEARTBEAT.touch()
        except Exception:
            # Healthcheck will fail if repeated DB errors stop useful progress.
            print('Publish worker: operation unavailable; retrying without logging secrets',flush=True)
            worked=False
        if not worked:time.sleep(2)


if __name__=='__main__':
    if '--healthcheck' in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time()-HEARTBEAT.stat().st_mtime<45 else 1)
    main()
