from sqlalchemy import text
from app.models import Event, ScriptJob


def test_review_render_publish_states_and_inbox_semantics(db):
    from app.models import ScriptSourceSnapshot, ScriptVersion, VideoJob, VideoVersion, TikTokAccount, PublishJob, utcnow
    with db() as s:
        event = Event(title='Full state chain', decision='selected')
        s.add(event); s.flush()
        def state():
            s.flush()
            return s.execute(text('SELECT status FROM pipeline_items WHERE event_id=:id'), {'id':event.id}).scalar()
        snapshot = ScriptSourceSnapshot(event_id=event.id, payload={}, digest='a'*64)
        s.add(snapshot); s.flush()
        script = ScriptVersion(event_id=event.id, version=1, snapshot_id=snapshot.id, provider='fake', model='fake',
            outcome='draft', status='needs_review', data={}, prompt_version='1', schema_version='1', prompt_hash='a'*64, schema_hash='b'*64)
        s.add(script)
        assert state() == 'SCRIPT_REVIEW'
        script.status='approved'
        assert state() == 'SCRIPT_APPROVED'
        job = VideoJob(script_version_id=script.id, idempotency_key='full-chain-render', request_hash='a'*64, input_snapshot={})
        s.add(job)
        assert state() == 'AUDIO_GENERATING'
        job.status='rendering'
        assert state() == 'RENDERING'
        job.status='failed'
        assert state() == 'FAILED'
        job.status='queued'
        assert state() == 'AUDIO_GENERATING'
        job.status='succeeded'
        video = VideoVersion(script_version_id=script.id, job_id=job.id, version=1, status='needs_review', config={},
            timeline={}, input_hash='a'*64, output_key='video/state-only.mp4', duration_seconds=45)
        s.add(video)
        assert state() == 'VIDEO_REVIEW'
        video.status='approved'
        assert state() == 'READY_TO_PUBLISH'
        account = TikTokAccount(open_id='state-test', display_name='State test', scopes=[], access_expires=utcnow(), refresh_expires=utcnow())
        s.add(account); s.flush()
        publish = PublishJob(video_version_id=video.id, account_id=account.id, idempotency_key='full-chain-publish', request_hash='a'*64, video_sha256='b'*64, snapshot={})
        s.add(publish)
        assert state() == 'PUBLISHING'
        publish.status='inbox'
        assert state() == 'PUBLISHING'
        publish.status='published'
        assert state() == 'PUBLISHED'


def test_state_tracks_legacy_worker_writes(db):
    with db() as s:
        event = Event(title='Pipeline')
        s.add(event); s.flush()
        eid = event.id
        assert s.execute(text('SELECT status FROM pipeline_items WHERE event_id=:id'), {'id':eid}).scalar() == 'CRAWLED'
        event.decision = 'selected'; s.flush()
        assert s.execute(text('SELECT status FROM pipeline_items WHERE event_id=:id'), {'id':eid}).scalar() == 'SELECTED'
        job = ScriptJob(event_id=eid, idempotency_key='pipeline-test', request_hash='x', provider='fake', model='fake', tone='neutral', target_seconds=45, timeout_seconds=30, output_limit=1024, prompt_text='test')
        s.add(job); s.flush()
        assert s.execute(text('SELECT status FROM pipeline_items WHERE event_id=:id'), {'id':eid}).scalar() == 'SCRIPT_GENERATING'
        job.status='failed'; job.attempts=2; s.flush()
        state = s.execute(text('SELECT status,retry_count,error_message FROM pipeline_items WHERE event_id=:id'), {'id':eid}).one()
        assert state.status == 'FAILED' and state.retry_count == 1 and state.error_message
        job.status='queued'; s.flush()
        state = s.execute(text('SELECT status,error_message FROM pipeline_items WHERE event_id=:id'), {'id':eid}).one()
        assert state.status == 'SCRIPT_GENERATING' and state.error_message is None
