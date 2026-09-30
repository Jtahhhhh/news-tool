import difflib
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_
from app.database import session_scope
from app.config import get_settings
from app.models import Event, ScriptJob, ScriptVersion, ScriptSourceSnapshot, ScriptReview
from app.services.script_service import (CreateRequest, enqueue_script, latest_version, save_version,
                                         review_version, renderable_version)

router = APIRouter()


def job_json(job):
    result = {name: (getattr(job, name).isoformat() if getattr(job, name) is not None and name.endswith('_at')
                   else getattr(job, name)) for name in (
        'id', 'event_id', 'status', 'provider', 'model', 'attempts', 'next_attempt_at', 'created_at',
        'finished_at', 'retry_started_at', 'error_kind', 'error', 'logs', 'kind', 'cancelled', 'reserved_microusd', 'reserved_output_tokens')}
    result['max_attempts'] = (job.routing or {}).get('max_attempts', 6)
    result['source_details'] = job.source_details
    result['repair_attempts'] = job.repair_attempts
    return result


def version_json(version):
    return {name: getattr(version, name) for name in (
        'id', 'event_id', 'version', 'parent_version_id', 'snapshot_id', 'job_id', 'provider', 'model',
        'origin', 'outcome', 'status', 'data', 'validation_errors', 'usage', 'elapsed_seconds',
        'prompt_version', 'schema_version', 'prompt_hash', 'schema_hash')}


def wants_json(request):
    return 'application/json' in request.headers.get('accept', '') or request.query_params.get('format') == 'json'


def render(request, name, **context):
    from app.main import templates
    return templates.TemplateResponse(request=request, name='scripts/' + name, context=context)


def configured_ui_settings(session):
    from app.services.llm_control import get_policy
    policy=get_policy(session)
    settings=get_settings().model_copy()
    settings.llm_provider=policy['routes'][0]['provider']
    for route in policy['routes']:
        setattr(settings,route['provider']+'_model',route['model'])
    return settings


@router.post('/script-jobs', status_code=202)
def create_job(payload: CreateRequest):
    with session_scope() as session:
        return job_json(enqueue_script(session, payload))


@router.get('/script-jobs/{job_id}')
def get_job(job_id: int):
    with session_scope() as session:
        job = session.get(ScriptJob, job_id)
        if not job:
            raise HTTPException(404, 'Không tìm thấy yêu cầu')
        result = job_json(job)
        version = session.scalar(select(ScriptVersion).where(ScriptVersion.job_id == job.id))
        result['version_id'] = version.id if version else None
        from app.models import LLMAttempt
        from app.services.llm_control import attempt_json
        result['history'] = [attempt_json(a) for a in session.scalars(select(LLMAttempt).where(LLMAttempt.job_id==job.id).order_by(LLMAttempt.number))]
        return result


@router.get('/scripts')
def list_scripts(request: Request, q: str = '', status: str = '', provider: str = '', page: int = 1):
    with session_scope() as session:
        # Rank in SQL so pagination does not load the entire version history.
        from sqlalchemy import func
        current = select(ScriptVersion.event_id, func.max(ScriptVersion.version).label('number')).group_by(ScriptVersion.event_id).subquery()
        query = select(ScriptVersion, Event).join(Event, Event.id == ScriptVersion.event_id).join(
            current, (current.c.event_id == ScriptVersion.event_id) & (current.c.number == ScriptVersion.version))
        if q:
            query = query.where(or_(Event.title.ilike(f'%{q}%'), ScriptVersion.data['title'].astext.ilike(f'%{q}%')))
        if status:
            query = query.where(or_(ScriptVersion.status == status, ScriptVersion.outcome == status))
        if provider:
            query = query.where(ScriptVersion.provider == provider)
        rows = session.execute(query.order_by(ScriptVersion.created_at.desc()).offset((max(1, page)-1)*30).limit(31)).all()
        jobs = session.scalars(select(ScriptJob).where(ScriptJob.kind=='script').order_by(ScriptJob.id.desc()).limit(20)).all()
        if wants_json(request):
            return {'scripts': [version_json(v) for v, e in rows[:30]], 'jobs': [job_json(j) for j in jobs], 'has_next': len(rows) > 30}
        return render(request, 'list.html', rows=rows[:30], jobs=jobs, q=q, status=status, provider=provider,
                      page=max(1, page), has_next=len(rows) > 30)


@router.get('/scripts/new')
def new_script(request: Request, event_id: int | None = None):
    with session_scope() as session:
        events = session.scalars(select(Event).where(Event.decision == 'selected').order_by(Event.id.desc())).all()
        return render(request, 'new.html', events=events, event_id=event_id, settings=configured_ui_settings(session))


def readable(data):
    if not data:
        return ''
    lines = [data.get(k, '') for k in ('title', 'hook', 'caption')]
    for scene in data.get('scenes', []):
        lines += [f'Cảnh {scene["scene_id"]} · {scene["seconds"]} giây', scene['narration'], scene['on_screen_text'], scene['visual_brief']]
        lines += ['Dẫn chứng: ' + ', '.join(scene['claim_ids'])]
    for claim in data.get('claims', []):
        lines += [f'Khẳng định {claim["claim_id"]}: {claim["text"]}']
        lines += [f'Nguồn {e["source_id"]}: {e["quote"]}' for e in claim['evidence']]
    lines += data.get('warnings', [])
    return '\n'.join(lines)


@router.get('/scripts/{event_id}')
def detail(request: Request, event_id: int, version_id: int | None = None, compare_id: int | None = None):
    with session_scope() as session:
        event = session.get(Event, event_id)
        if not event:
            raise HTTPException(404, 'Không tìm thấy nhóm tin')
        versions = session.scalars(select(ScriptVersion).where(ScriptVersion.event_id == event_id)
                                  .order_by(ScriptVersion.version.desc())).all()
        current = next((v for v in versions if v.id == version_id), None) if version_id else (versions[0] if versions else None)
        if version_id and not current:
            raise HTTPException(404, 'Không tìm thấy phiên bản')
        snapshot = session.get(ScriptSourceSnapshot, current.snapshot_id) if current else None
        jobs = session.scalars(select(ScriptJob).where(ScriptJob.event_id == event_id).order_by(ScriptJob.id.desc()).limit(20)).all()
        reviews = session.scalars(select(ScriptReview).join(ScriptVersion).where(ScriptVersion.event_id == event_id)
                                  .order_by(ScriptReview.id.desc())).all()
        eligible = False
        if current and versions and current.id == versions[0].id:
            try:
                renderable_version(session, event_id)
                eligible = True
            except HTTPException:
                pass
        if wants_json(request):
            return {'event_id': event_id, 'current': version_json(current) if current else None,
                    'versions': [version_json(v) for v in versions], 'snapshot': snapshot.payload if snapshot else None,
                    'jobs': [job_json(j) for j in jobs], 'render_eligible': eligible,
                    'reviews': [{'version_id': r.version_id, 'decision': r.decision, 'reviewer': r.reviewer,
                                 'comment': r.comment, 'created_at': r.created_at.isoformat()} for r in reviews]}
        previous = next((v for v in versions if v.id == compare_id), None) if compare_id else next((v for v in versions if current and v.version < current.version), None)
        diff = '\n'.join(difflib.unified_diff(readable(previous.data).splitlines(), readable(current.data).splitlines(),
                          fromfile=f'Bản {previous.version}', tofile=f'Bản {current.version}', lineterm='')) if current and previous else ''
        return render(request, 'detail.html', event=event, current=current, versions=versions, snapshot=snapshot,
                      jobs=jobs, reviews=reviews, diff=diff, eligible=eligible, settings=configured_ui_settings(session),
                      editable=bool(current and current == versions[0] and current.outcome == 'draft'),
                      latest_id=versions[0].id if versions else None)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_version_id: int
    data: dict


@router.post('/scripts/{event_id}/versions', status_code=201)
def edit_script(event_id: int, payload: EditRequest):
    with session_scope() as session:
        return version_json(save_version(session, event_id, payload.base_version_id, payload.data))


@router.post('/scripts/{event_id}/regenerate', status_code=202)
def regenerate(event_id: int, payload: CreateRequest):
    if event_id != payload.event_id:
        raise HTTPException(422, 'Nhóm tin không khớp')
    with session_scope() as session:
        return job_json(enqueue_script(session, payload))


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version_id: int
    decision: Literal['approved', 'rejected']
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(default='', max_length=4000)


@router.post('/scripts/{event_id}/reviews', status_code=201)
def review_script(event_id: int, payload: ReviewRequest):
    with session_scope() as session:
        review = review_version(session, event_id, **payload.model_dump())
        return {'id': review.id, 'version_id': review.version_id, 'decision': review.decision}
