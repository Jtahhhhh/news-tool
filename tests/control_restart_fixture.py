"""Prepare/check an isolated Compose restart scenario; never sends a live request."""
import sys
import uuid
from sqlalchemy import select, func
from app.database import engine, session_scope
from app.models import LLMQuotaState, ScriptJob, LLMAttempt, ScriptVersion
from app.services.script_service import CreateRequest, enqueue_script

assert engine.url.database.endswith('_ui_test'), 'Requires isolated UI test database'
with session_scope() as session:
    if sys.argv[1] == 'prepare':
        for quota in session.scalars(select(LLMQuotaState)):
            quota.configured_limit = quota.window_used
            quota.window_seconds = None
        job = enqueue_script(session, CreateRequest(event_id=1, provider='gemini',
            idempotency_key='restart-' + uuid.uuid4().hex))
        print('Prepared job', job.id)
    else:
        job = session.scalar(select(ScriptJob).order_by(ScriptJob.id.desc()))
        assert job.status == 'waiting_quota' and job.attempts == 0
        assert job.next_attempt_at is None
        assert session.scalar(select(func.count()).select_from(LLMAttempt)) >= 2
        assert session.get(ScriptVersion, 2).status == 'approved'
        print('PASS: waiting job, zero extra sends, attempt history and approved review persisted')
