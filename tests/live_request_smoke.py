"""Explicit live smoke, ONE durable Gemini job, at most six sends, no fallback.

Only run manually with DATABASE_URL pointing at news_tool_smoke_test. Resuming
uses the same idempotent job; it never silently creates a second billable run.
"""
import json
import time
from pathlib import Path
from sqlalchemy import select
from app.database import engine, session_scope
from app.models import Source, Event, Article, ScriptJob, ScriptVersion, ScriptSourceSnapshot, LLMAttempt, LLMPolicy, utcnow
from app.services import llm_control as control
from app.services.script_service import CreateRequest, enqueue_script, claim_script, execute_script, recover_scripts
from app.routes.scripts import job_json
from app.start import migrate

KEY = 'request-trace-smoke-0004'


def main():
    assert engine.url.database == 'news_tool_smoke_test', 'Dedicated smoke database required'
    migrate()
    with session_scope() as session:
        control.sync_credentials(session)
        job=session.scalar(select(ScriptJob).where(ScriptJob.idempotency_key==KEY))
        if not job:
            policy=control.get_policy(session);policy.update(fallback_enabled=False,max_attempts=6,max_output_tokens_total=49152)
            session.get(LLMPolicy,1).data=policy
            source=Source(name='[SMOKE] Nguồn giả lập kiểm tra API',url='https://example.com/api-smoke',enabled=False)
            event=Event(title='[SMOKE] Thư viện mở phòng đọc',decision='selected')
            session.add_all([source,event]);session.flush()
            session.add(Article(source_id=source.id,event_id=event.id,title=event.title,canonical_url=source.url,
                fingerprint=KEY,summary='Thư viện thành phố mở thêm phòng đọc với 120 chỗ ngồi. Đại diện thư viện cho biết khu vực mới phục vụ bạn đọc vào các ngày trong tuần. Đây là nguồn giả lập để kiểm tra API, không phải tin thật để xuất bản.'))
            job=enqueue_script(session,CreateRequest(event_id=event.id,provider='gemini',target_seconds=15,idempotency_key=KEY))
        job_id=job.id
    deadline=time.monotonic()+930
    previous=None
    while time.monotonic()<deadline:
        with session_scope() as session:
            recover_scripts(session)
            job=session.get(ScriptJob,job_id)
            state=(job.status,job.attempts)
            if state!=previous:
                print(json.dumps(dict(job_id=job.id,status=job.status,attempts=job.attempts,next_attempt_at=str(job.next_attempt_at))),flush=True)
                previous=state
            if job.status in ('succeeded','failed','unknown_outcome') or (job.status=='waiting_quota' and not job.next_attempt_at):break
            claim=claim_script(session)
        if claim:execute_script(*claim)
        else:time.sleep(1)  # Test harness polling; production worker never sleeps for a job retry.
    with session_scope() as session:
        job=session.get(ScriptJob,job_id)
        version=session.scalar(select(ScriptVersion).where(ScriptVersion.job_id==job_id))
        report=dict(at=utcnow().isoformat(),live_call=job.attempts>0,job=job_json(job),
            attempts=[control.attempt_json(a) for a in session.scalars(select(LLMAttempt).where(LLMAttempt.job_id==job_id).order_by(LLMAttempt.number))],
            structural_pass=bool(version and not version.validation_errors),review_status=version.status if version else None,
            output=version.data if version else None,
            source_snapshot=session.get(ScriptSourceSnapshot,job.snapshot_id).payload,
            cost='unknown; verify provider dashboard')
        Path('/tmp/request-trace-live-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status=job.status,attempts=job.attempts,structural_pass=report['structural_pass'],review_status=report['review_status'])),flush=True)


if __name__=='__main__':main()
