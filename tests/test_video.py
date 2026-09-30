from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import select

from app.models import VideoJob, VideoReview, VideoVersion, utcnow
from app.services.video_service import VIDEOS, normalize, recover


@pytest.fixture
def video_event(db,monkeypatch):
    from tests.test_scripts import selected_event
    return selected_event.__wrapped__(db,monkeypatch)


def test_pronunciation_rules_cover_number_name_and_mixed_english():
    assert normalize('VieNeu đọc 120 API với FFmpeg', {
        'VieNeu':'Vi Nơ', '120':'một trăm hai mươi', 'API':'ây pi ai', 'FFmpeg':'ép ép em peg'
    }) == 'Vi Nơ đọc một trăm hai mươi ây pi ai với ép ép em peg'


def test_video_gate_snapshot_idempotency_and_narration_guard(db, client, video_event):
    from tests.test_scripts import request_for, run_queued
    from app.models import ScriptVersion
    with db() as session: __import__('app.services.script_service', fromlist=['enqueue_script']).enqueue_script(session, request_for(video_event))
    run_queued(db)
    with db() as session:
        script=session.scalar(select(ScriptVersion));script.status='approved';script_id=script.id;scene=script.data['scenes'][0]
    assert f'Sự kiện #{video_event}' not in client.get('/videos').text
    assert f'Sự kiện #{video_event}' in client.get('/videos?include_test=true').text
    base={'script_version_id':script_id,'idempotency_key':'video-key-0001','quality':'draft'}
    assert client.post('/video-jobs',json=base).status_code==409  # fake script needs an explicit test label
    response=client.post('/video-jobs',json={**base,'test_only':True})
    assert response.status_code==202,response.text
    assert client.post('/video-jobs',json={**base,'test_only':True}).json()['id']==response.json()['id']
    with db() as session:
        job=session.get(VideoJob,response.json()['id']);job.status='cancelled';job.finished_at=utcnow()
    changed={**scene,'narration':'Nội dung đã bị thay đổi','asset_id':None,'crop':{'x':.5,'y':.5,'scale':1},'illustration_label':True}
    bad=client.post('/video-jobs',json={**base,'idempotency_key':'video-key-0002','test_only':True,'scenes':[changed]})
    assert bad.status_code==409 and 'kịch bản mới' in bad.json()['detail']


def test_recovery_and_version_review_do_not_inherit_approval(db, client, video_event):
    from tests.test_scripts import request_for, run_queued
    from app.models import ScriptVersion
    from app.services.script_service import enqueue_script
    with db() as session: enqueue_script(session,request_for(video_event))
    run_queued(db)
    with db() as session:
        script=session.scalar(select(ScriptVersion));script.status='approved'
        job=VideoJob(script_version_id=script.id,idempotency_key='recover-key-1',request_hash='a'*64,status='rendering',stage='rendering',progress=55,input_snapshot={},checkpoint={'audio_complete':True},logs=[],lease_until=utcnow()-timedelta(seconds=1))
        session.add(job);session.flush();recover(session);assert job.status=='queued'
        job.status='failed'
        paths=[]
        for number in (1,2):
            path=VIDEOS/f'test-review-{script.id}-{number}.mp4'
            __import__('subprocess').run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=s=360x640:r=25:d=3','-c:v','libx264',str(path)],check=True)
            paths.append(path)
            version=VideoVersion(script_version_id=script.id,version=number,parent_version_id=None if number==1 else 1,status='needs_review',config={'width':720,'height':1280},timeline={},input_hash=str(number)*64,output_key=str(path.relative_to(VIDEOS.parent)).replace('\\','/'),probe={'format':{'duration':'1'}},duration_seconds=1,test_only=True)
            session.add(version);session.flush()
        first_id,second_id=session.scalars(select(VideoVersion.id).order_by(VideoVersion.id)).all()
    old=client.post(f'/videos/{first_id}/reviews',json={'decision':'approved','reviewer':'QA','comment':''})
    assert old.status_code==409
    assert client.post(f'/videos/{second_id}/reviews',json={'decision':'approved','reviewer':'QA','comment':'đạt'}).status_code==201
    with db() as session:
        assert session.get(VideoVersion,first_id).status=='needs_review'
        assert session.get(VideoVersion,second_id).status=='approved'
        assert session.scalar(select(VideoReview)).video_version_id==second_id
    for path in paths:path.unlink(missing_ok=True)


def test_broken_upload_is_rejected(client):
    response=client.post('/assets',files={'file':('bad.png',b'not an image','image/png')},data={'license':'user-provided'})
    assert response.status_code==422 and 'hỏng' in response.json()['detail']
