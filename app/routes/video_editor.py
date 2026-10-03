from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.database import session_scope
from app.models import ScriptVersion, VideoVersion, MediaAsset
from app.services.video_service import DATA, enqueue
from app.services.video.project import editor_source, load_draft, library, resolve
from app.services.video.validator import Composition, Target
from app.services.video.timeline import duration

router = APIRouter()


class AnalysisRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target: Target
    sentence_boundaries: list[float] = Field(default_factory=list,max_length=1000)


@router.post('/assets/{asset_id}/analysis')
def analyze_asset(asset_id:int,payload:AnalysisRequest):
    import math
    import subprocess
    from app.services.video.project import describe
    from app.services.video.analysis import analyze
    with session_scope() as session:
        asset=session.get(MediaAsset,asset_id)
        if not asset or asset.media_type!='video': raise HTTPException(422,'Chọn nguồn video để phân tích')
        source=describe(asset,DATA)
        if source['duration']>7200: raise HTTPException(422,'Phân tích hỗ trợ nguồn tối đa 2 giờ')
        if any(not math.isfinite(t) or t<0 or t>source['duration'] for t in payload.sentence_boundaries):
            raise HTTPException(422,'Mốc câu nằm ngoài nguồn video')
        key=f"{payload.target.width}x{payload.target.height}"
        cache=asset.probe.get('analysis',{})
        if not payload.sentence_boundaries and key in cache: return cache[key]
        try:
            result=analyze(DATA/asset.storage_key,source,payload.target.model_dump(),payload.sentence_boundaries)
        except (ValueError,subprocess.TimeoutExpired) as e:
            raise HTTPException(422,'Phân tích chưa hoàn tất: '+str(e)[:200]) from e
        if not payload.sentence_boundaries: asset.probe={**asset.probe,'analysis':{**cache,key:result}}
        return result


def project_data(session, script_id):
    script=session.get(ScriptVersion,script_id)
    if not script:
        raise HTTPException(404,'Không tìm thấy kịch bản')
    job=editor_source(session,script_id)
    latest=session.scalar(select(VideoVersion).where(VideoVersion.script_version_id==script_id).order_by(VideoVersion.version.desc()))
    return dict(script_id=script_id,title=script.data['title'],job_id=job.id,
                revision=job.checkpoint.get('editor_revision',0),composition=load_draft(job),
                media=library(session,job,DATA),parent_version_id=latest.id if latest else None,
                test_only=job.input_snapshot['config'].get('test_only',False))


@router.get('/videos/editor/{script_id}')
def editor(request:Request,script_id:int):
    from app.main import templates
    with session_scope() as session:
        data=project_data(session,script_id)
        return templates.TemplateResponse(request=request,name='video/editor.html',context={'editor_data':data})


@router.get('/videos/editor/{script_id}/data')
def data(script_id:int):
    with session_scope() as session:
        return project_data(session,script_id)


class SaveDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:int
    revision:int=Field(ge=0)
    composition:Composition


@router.post('/videos/editor/{script_id}/draft')
def save(script_id:int,payload:SaveDraft):
    with session_scope() as session:
        job=editor_source(session,script_id,payload.job_id,lock=True)
        if job.checkpoint.get('editor_revision',0)!=payload.revision:
            raise HTTPException(409,'Nháp đã được sửa ở tab khác. Tải lại trang trước khi lưu tiếp.')
        composition=payload.composition.model_dump(by_alias=True)
        resolve(session,composition,DATA)
        revision=payload.revision+1
        job.checkpoint={**job.checkpoint,'editor_draft':composition,'editor_revision':revision}
        return {'revision':revision}


class Export(SaveDraft):
    idempotency_key:str=Field(min_length=8,max_length=128)
    parent_version_id:int|None=None


@router.post('/videos/editor/{script_id}/export',status_code=202)
def export(script_id:int,payload:Export):
    from app.routes.video import job_json
    with session_scope() as session:
        source=editor_source(session,script_id,payload.job_id,lock=True)
        if source.checkpoint.get('editor_revision',0)!=payload.revision:
            raise HTTPException(409,'Nháp đã thay đổi ở tab khác; tải lại trước khi export')
        composition=payload.composition.model_dump(by_alias=True)
        if duration(composition)<=0:
            raise HTTPException(422,'Timeline trống')
        if payload.parent_version_id:
            parent=session.get(VideoVersion,payload.parent_version_id)
            if not parent or parent.script_version_id!=script_id:
                raise HTTPException(409,'Phiên bản gốc không thuộc kịch bản này')
        config={**source.input_snapshot['config'],'preview_only':False,'quality':'final','width':1080,'height':1920}
        job=enqueue(session,script_id,payload.idempotency_key,config,
                    parent_version_id=payload.parent_version_id,composition=composition)
        return job_json(job)
