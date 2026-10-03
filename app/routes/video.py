import hashlib,mimetypes,subprocess,tempfile,uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,File,Form,HTTPException,Request,UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.database import session_scope
from app.models import ScriptVersion,ScriptReview,VideoJob,VideoVersion,VideoReview,MediaAsset,TTSAudio,utcnow
from app.services.video_service import DATA,ASSETS,AUDIO,VIDEOS,ACTIVE,enqueue,digest
router=APIRouter()

def job_json(j):return {k:(getattr(j,k).isoformat() if getattr(j,k) and k.endswith('_at') else getattr(j,k)) for k in ('id','script_version_id','status','stage','progress','checkpoint','error','created_at','finished_at')}
def version_json(v):return {k:getattr(v,k) for k in ('id','script_version_id','job_id','version','parent_version_id','status','config','timeline','input_hash','output_key','probe','duration_seconds','test_only')}

@router.get('/videos')
def videos(request:Request,include_test:bool=False):
    from app.main import templates
    with session_scope() as s:
        versions=s.scalars(select(VideoVersion).order_by(VideoVersion.id.desc()).limit(30)).all()
        rows=s.scalars(select(ScriptVersion).order_by(ScriptVersion.event_id,ScriptVersion.version.desc())).all()
        latest=[];seen=set()
        for row in rows:
            if row.event_id not in seen:latest.append(row);seen.add(row.event_id)
        approved_ids=set(s.scalars(select(ScriptReview.version_id).where(ScriptReview.decision=='approved')).all())
        approved=[row for row in rows if row.status=='approved' and row.outcome=='draft' and (row.id in approved_ids or (include_test and row.provider=='fake'))]
        scripts=[];approved_events=set()
        for row in approved:
            if row.event_id not in approved_events and (include_test or row.provider!='fake'):
                scripts.append(row);approved_events.add(row.event_id)
        unavailable=[row for row in latest if row.event_id not in approved_events and row.provider!='fake']
        jobs=s.scalars(select(VideoJob).order_by(VideoJob.id.desc()).limit(30)).all()
        assets=s.scalars(select(MediaAsset).order_by(MediaAsset.id.desc())).all()
        video_input={'scripts':{str(row.id):{'data':row.data,'requires_test':row.provider=='fake','event_id':row.event_id,'version':row.version} for row in scripts},'assets':[{'id':a.id,'filename':a.filename,'media_type':a.media_type,'test_only':a.test_only} for a in assets]}
        return templates.TemplateResponse(request=request,name='video/list.html',context=dict(versions=versions,scripts=scripts,unavailable=unavailable,jobs=jobs,assets=assets,video_input=video_input,include_test=include_test))

class CreateVideo(BaseModel):
    model_config=ConfigDict(extra='forbid')
    script_version_id:int;idempotency_key:str=Field(min_length=8,max_length=128)
    voice:str=Field(default='Hải Đăng',max_length=100);template:str=Field(default='news-dark',max_length=50)
    quality:Literal['draft','final']='draft';pronunciation:dict[str,str]=Field(default_factory=dict)
    test_only:bool=False;preview_only:bool=False;scenes:list[dict]|None=None;parent_version_id:int|None=None

@router.post('/video-jobs',status_code=202)
def create(payload:CreateVideo):
    cfg=payload.model_dump(exclude={'script_version_id','idempotency_key','scenes','parent_version_id'})
    cfg.update(width=720 if payload.quality=='draft' else 1080,height=1280 if payload.quality=='draft' else 1920)
    with session_scope() as s:
        script=s.get(ScriptVersion,payload.script_version_id)
        if script and script.provider=='fake' and not payload.test_only:
            raise HTTPException(409,'Kịch bản giả lập chỉ được dựng khi bật nhãn dữ liệu test')
        scenes=payload.scenes
        composition=None
        if payload.parent_version_id:
            parent=s.get(VideoVersion,payload.parent_version_id)
            if not parent or parent.script_version_id!=payload.script_version_id:raise HTTPException(409,'Phiên bản gốc không thuộc kịch bản này')
            # A re-render starts from the immutable prior snapshot and only changes
            # the selected quality. Test labels and pronunciation rules cannot be lost.
            cfg={**parent.config,'quality':payload.quality,'width':cfg['width'],'height':cfg['height']}
            scenes=scenes or parent.timeline.get('scenes')
            if parent.timeline.get('version') in (1,2):
                composition=parent.timeline
                cfg['preview_only']=False
        return job_json(enqueue(s,payload.script_version_id,payload.idempotency_key,cfg,scenes,payload.parent_version_id,composition=composition))

@router.get('/video-jobs/{job_id}')
def job(job_id:int):
    with session_scope() as s:
        row=s.get(VideoJob,job_id)
        if not row:raise HTTPException(404,'Không tìm thấy job video')
        result=job_json(row);version=s.scalar(select(VideoVersion).where(VideoVersion.job_id==job_id));result['video_version_id']=version.id if version else None;return result

@router.post('/video-jobs/{job_id}/cancel')
def cancel(job_id:int):
    with session_scope() as s:
        row=s.scalar(select(VideoJob).where(VideoJob.id==job_id).with_for_update())
        if not row:raise HTTPException(404,'Không tìm thấy job')
        if row.status not in ACTIVE:raise HTTPException(409,'Job đã kết thúc')
        row.cancelled=True;row.status='cancelled';row.stage='cancelled';row.finished_at=utcnow();return job_json(row)

@router.post('/video-jobs/{job_id}/retry')
def retry(job_id:int):
    with session_scope() as s:
        row=s.scalar(select(VideoJob).where(VideoJob.id==job_id).with_for_update())
        if not row or row.status not in ('failed','cancelled'):raise HTTPException(409,'Chỉ thử lại job lỗi hoặc đã hủy')
        if s.scalar(select(VideoJob.id).where(VideoJob.script_version_id==row.script_version_id,VideoJob.id!=row.id,VideoJob.status.in_(ACTIVE))):raise HTTPException(409,'Đã có job khác hoạt động')
        row.status='queued';row.stage='queued';row.progress=0;row.cancelled=False;row.error=None;row.finished_at=None;return job_json(row)

@router.post('/assets',status_code=201)
async def upload(file:UploadFile=File(...),author:str=Form(''),license:str=Form('user-provided'),test_only:bool=Form(False)):
    from app.services.video.media_probe import EXTENSIONS, audit, prepare
    from starlette.concurrency import run_in_threadpool
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in EXTENSIONS:raise HTTPException(415,'Định dạng chưa hỗ trợ. Chọn video, ảnh hoặc audio phổ biến.')
    from app.config import get_settings
    from app.services.video.project import describe
    limit=get_settings().max_media_upload_mb*1024*1024
    temporary=ASSETS/(uuid.uuid4().hex+'.uploading'+suffix)
    size=0;checksum=hashlib.sha256()
    try:
        with temporary.open('wb') as target:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>limit:raise HTTPException(422,f'File vượt {get_settings().max_media_upload_mb} MB')
                checksum.update(chunk);target.write(chunk)
        if not size:raise HTTPException(422,'File rỗng')
        media_probe=await run_in_threadpool(audit,temporary)
        kind=media_probe['type']
        key=checksum.hexdigest();path=ASSETS/(key+suffix)
        if not path.exists():temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
    proxy=await run_in_threadpool(prepare,path,media_probe)
    with session_scope() as s:
        row=s.scalar(select(MediaAsset).where(MediaAsset.storage_key==str(path.relative_to(DATA)).replace('\\','/')))
        if not row:row=MediaAsset(filename=Path(file.filename or 'asset').name[:255],storage_key=str(path.relative_to(DATA)).replace('\\','/'),media_type=kind,source='upload',author=author[:200],license=license[:200],sha256=key,test_only=test_only);s.add(row);s.flush()
        elif test_only:row.test_only=True
        row.probe=media_probe
        row.proxy_key=str(proxy.relative_to(DATA)).replace(chr(92),'/') if proxy else None
        return {'id':row.id,'filename':row.filename,'media_type':row.media_type,**describe(row,DATA)}

@router.get('/media/{kind}/{name}')
def media(kind:Literal['assets','audio','video'],name:str):
    if Path(name).name!=name:raise HTTPException(404,'Không tìm thấy file')
    path={'assets':ASSETS,'audio':AUDIO,'video':VIDEOS}[kind]/name
    if not path.is_file():raise HTTPException(404,'Không tìm thấy file')
    return FileResponse(path,media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

@router.get('/videos/{version_id}')
def detail(request:Request,version_id:int):
    from app.main import templates
    with session_scope() as s:
        version=s.get(VideoVersion,version_id)
        if not version:raise HTTPException(404,'Không tìm thấy phiên bản video')
        history=s.scalars(select(VideoVersion).where(VideoVersion.script_version_id==version.script_version_id).order_by(VideoVersion.version.desc())).all()
        reviews=s.scalars(select(VideoReview).join(VideoVersion).where(VideoVersion.script_version_id==version.script_version_id).order_by(VideoReview.id.desc())).all()
        assets=s.scalars(select(MediaAsset).order_by(MediaAsset.id.desc())).all()
        return templates.TemplateResponse(request=request,name='video/detail.html',context=dict(version=version,history=history,reviews=reviews,assets=assets))

class Review(BaseModel):
    decision:Literal['approved','rejected'];reviewer:str=Field(min_length=1,max_length=100);comment:str=Field(default='',max_length=4000)
@router.post('/videos/{version_id}/reviews',status_code=201)
def review(version_id:int,payload:Review):
    with session_scope() as s:
        version=s.scalar(select(VideoVersion).where(VideoVersion.id==version_id).with_for_update())
        if not version or (version.status!='needs_review' and not (version.status=='approved' and not version.output_sha256)):
            raise HTTPException(409,'Chỉ duyệt phiên bản đang chờ hoặc bản cũ chưa có checksum')
        latest=s.scalar(select(VideoVersion).where(VideoVersion.script_version_id==version.script_version_id).order_by(VideoVersion.version.desc()))
        if latest.id!=version.id:raise HTTPException(409,'Chỉ duyệt phiên bản video mới nhất')
        from app.services.media_integrity import seal_review
        checksum=seal_review(version) if payload.decision=='approved' else None
        version.status=payload.decision;row=VideoReview(video_version_id=version.id,output_sha256=checksum,**payload.model_dump());s.add(row);s.flush();return {'id':row.id,'decision':row.decision}
