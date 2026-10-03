import hashlib,json,os,uuid
from datetime import timedelta
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from app.models import (ScriptVersion,ScriptReview,VideoJob,VideoVersion,MediaAsset,utcnow)

DATA=Path(os.getenv('MEDIA_ROOT','/data')); ASSETS=DATA/'assets'; AUDIO=DATA/'audio'; VIDEOS=DATA/'video'; TMP=DATA/'tmp'
for folder in (ASSETS,AUDIO,VIDEOS,TMP):folder.mkdir(parents=True,exist_ok=True)
ACTIVE=('queued','generating_audio','rendering')
DEFAULTS=dict(voice='Hải Đăng',template='news-dark',width=720,height=1280,quality='draft',target_seconds=45,
              pronunciation={},test_only=False,model='vieneu-v3-turbo',model_version='v3-turbo')

def digest(value):
    raw=value if isinstance(value,(bytes,bytearray)) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if isinstance(raw,str):raw=raw.encode()
    return hashlib.sha256(raw).hexdigest()

from app.services.video.audio import normalize, synthesize
from app.services.video.project import snapshot
from app.services.video.ffmpeg import probe


def enqueue(session,script_version_id,idempotency_key,config=None,scenes=None,parent_version_id=None,composition=None):
    existing=session.scalar(select(VideoJob).where(VideoJob.idempotency_key==idempotency_key))
    script=session.get(ScriptVersion,script_version_id)
    if not script or script.status!='approved' or script.outcome!='draft':raise HTTPException(409,'Chỉ dựng từ kịch bản hợp lệ đã duyệt')
    if script.provider != 'fake':
        from app.services.script_service import checked_output
        checked_output(session,script,script.data)
    audited=session.scalar(select(ScriptReview.id).where(ScriptReview.version_id==script.id,ScriptReview.decision=='approved').order_by(ScriptReview.id.desc()).limit(1))
    if not audited and not (script.provider=='fake' and (config or {}).get('test_only')):raise HTTPException(409,'Kịch bản chưa có quyết định duyệt hợp lệ')
    latest=session.scalar(select(ScriptVersion.id).join(ScriptReview,ScriptReview.version_id==ScriptVersion.id).where(ScriptVersion.event_id==script.event_id,ScriptVersion.status=='approved',ScriptVersion.outcome=='draft',ScriptReview.decision=='approved').order_by(ScriptVersion.version.desc()).limit(1))
    if audited and latest!=script.id:raise HTTPException(409,'Chỉ dựng bản mới nhất trong các kịch bản đã duyệt')
    config=dict(config or {});source_scenes={str(row['scene_id']):row for row in script.data['scenes']}
    if scenes:
        if {str(row.get('scene_id')) for row in scenes}!={*source_scenes}:raise HTTPException(422,'Danh sách cảnh không khớp kịch bản đã duyệt')
        for row in scenes:
            original=source_scenes[str(row['scene_id'])]
            if row.get('narration')!=original['narration']:raise HTTPException(409,'Lời đọc đã đổi ý nghĩa; hãy tạo và duyệt phiên bản kịch bản mới')
            asset_id=row.get('asset_id')
            if asset_id:
                asset=session.get(MediaAsset,int(asset_id))
                if not asset:raise HTTPException(422,'Asset của cảnh không tồn tại')
                if asset.test_only:config['test_only']=True
    data=snapshot(script,config,scenes)
    if composition is not None:
        from app.services.video.validator import validate
        from app.services.video.project import resolve
        composition=validate(composition)
        _,test_assets=resolve(session,composition,DATA)
        data['composition']=composition
        data['config']['test_only']=bool(data['config'].get('test_only') or test_assets or script.provider=='fake')
    if parent_version_id:data['parent_version_id']=parent_version_id
    request_hash=digest(data)
    if existing:
        if existing.request_hash!=request_hash:raise HTTPException(409,'Idempotency key đã dùng cho cấu hình khác')
        return existing
    if session.scalar(select(VideoJob.id).where(VideoJob.script_version_id==script.id,VideoJob.status.in_(ACTIVE))):
        raise HTTPException(409,'Kịch bản đang có job video hoạt động')
    if parent_version_id:data['parent_version_id']=parent_version_id
    job=VideoJob(script_version_id=script.id,idempotency_key=idempotency_key,request_hash=request_hash,
        input_snapshot=data,checkpoint={},logs=[]);session.add(job);session.flush();return job

def claim(session):
    job=session.scalar(select(VideoJob).where(VideoJob.status=='queued',VideoJob.cancelled.is_(False))
        .order_by(VideoJob.id).with_for_update(skip_locked=True).limit(1))
    if not job:return None
    job.status='generating_audio';job.stage='generating_audio';job.progress=5;job.owner=uuid.uuid4().hex
    job.lease_until=utcnow()+timedelta(minutes=30);return job.id,job.owner


def esc(value):return value.replace('\\','/').replace(':','\\:').replace("'","\\'")
def ensure_active(job,session):
    session.refresh(job,['cancelled'])
    if job.cancelled:raise RuntimeError('Job đã được hủy')
from app.services.video.render import render


def execute(job_id,owner):
    from app.database import SessionLocal,session_scope
    try:
        # render() commits after audio generation so that a container restart can
        # resume from the durable checkpoint instead of losing completed TTS work.
        with SessionLocal() as session:
            job=session.scalar(select(VideoJob).where(VideoJob.id==job_id,VideoJob.owner==owner).with_for_update())
            if not job or job.cancelled:return
            render(job,session)
            session.commit()
    except Exception as exc:
        with session_scope() as session:
            job=session.get(VideoJob,job_id)
            if job and job.owner==owner:
                if not job.cancelled:job.status='failed';job.stage='failed';job.error=str(exc)[-2000:]
                job.finished_at=utcnow();job.owner=None;job.lease_until=None
                job.logs=[*job.logs,{'stage':job.stage,'at':job.finished_at.isoformat(),'error':str(exc)[-500:]}]

def recover(session):
    for job in session.scalars(select(VideoJob).where(VideoJob.status.in_(('generating_audio','rendering')),VideoJob.lease_until<utcnow()).with_for_update(skip_locked=True)):
        version=session.scalar(select(VideoVersion).where(VideoVersion.job_id==job.id))
        if version and (DATA/version.output_key).exists():job.status='succeeded';job.progress=100;job.stage='complete'
        else:job.status='queued';job.stage='recovered';job.owner=None;job.lease_until=None
