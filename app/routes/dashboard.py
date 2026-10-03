"""Version-oriented dashboard API; mutations reuse the existing review gates."""
from datetime import datetime, timezone
from typing import Literal
import uuid
import shutil
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, union_all, literal
from app.database import session_scope
from app.models import Article, Event, Source, Job, ScriptJob, ScriptVersion, VideoJob, VideoVersion, PublishJob
from app.routes.scripts import version_json as script_json
from app.routes.video import version_json as video_json
from app.services.script_service import CreateRequest, enqueue_script, save_version, review_version

router = APIRouter(prefix='/api')
JOB_MODELS = {'crawl': Job, 'script': ScriptJob, 'render': VideoJob, 'publish': PublishJob}
ACTIVE = ('queued', 'running', 'retry', 'retry_wait', 'waiting_quota', 'generating_audio', 'rendering', 'initializing', 'uploading', 'transferring', 'polling', 'reconcile')


def require(session, model, id):
    row = session.get(model, id)
    if row is None:
        raise HTTPException(404, 'Không tìm thấy dữ liệu')
    return row


def job_union():
    return union_all(*[select(literal(kind).label('job_type'), model.id,
        model.status, model.created_at,
        (model.error.is_not(None)).label('has_error')) for kind, model in JOB_MODELS.items()]).subquery()


@router.get('/dashboard/summary')
def summary():
    with session_scope() as s:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count = lambda model, *where: s.scalar(select(func.count()).select_from(model).where(*where))
        jobs = job_union()
        return dict(articles_today=count(Article, Article.collected_at >= today),
            scripts_generated=count(ScriptVersion, ScriptVersion.outcome == 'draft'),
            videos_rendered=count(VideoVersion), published=count(PublishJob, PublishJob.status == 'published'),
            failed_jobs=s.scalar(select(func.count()).select_from(jobs).where(jobs.c.status.in_(('failed', 'unknown_outcome')))),
            queue_size=s.scalar(select(func.count()).select_from(jobs).where(jobs.c.status.in_(ACTIVE))))


@router.get('/dashboard/pipeline')
def pipeline():
    # These are stage totals, not a claim that every version is a unique story.
    with session_scope() as s:
        stages = [('Crawled', Article, None), ('Selected', Event, Event.decision == 'selected'),
            ('Script', ScriptVersion, ScriptVersion.outcome == 'draft'),
            ('Approved', ScriptVersion, ScriptVersion.status == 'approved'),
            ('Rendered', VideoVersion, None), ('Published', PublishJob, PublishJob.status == 'published')]
        return [{'stage': name, 'count': s.scalar(select(func.count()).select_from(model).where(condition if condition is not None else True))} for name, model, condition in stages]


@router.get('/dashboard/activity')
def activity():
    with session_scope() as s:
        q = job_union()
        return [dict(row) for row in s.execute(select(q).order_by(q.c.created_at.desc(), q.c.job_type, q.c.id.desc()).limit(20)).mappings()]


@router.get('/dashboard/states')
def states():
    from sqlalchemy import text
    with session_scope() as s:
        return [dict(row) for row in s.execute(text('SELECT status, count(*) AS count FROM pipeline_items GROUP BY status ORDER BY status')).mappings()]


@router.get('/health')
@router.get('/health/services')
def services():
    from sqlalchemy import text
    from app.config import get_settings
    from app.services.video_service import DATA
    checks = {'API': 'ok', 'PostgreSQL': 'unavailable', 'Storage': 'ok' if DATA.is_dir() else 'unavailable',
              'FFmpeg': 'ok' if shutil.which('ffmpeg') else 'unavailable',
              'LLM': 'not_probed', 'TTS': 'not_probed', 'TikTok': 'not_probed'}
    try:
        with session_scope() as s:
            s.execute(text('SELECT 1'))
        checks['PostgreSQL'] = 'ok'
    except Exception:
        pass
    return {'environment': get_settings().app_env, 'services': checks}


def article_json(a):
    return dict(id=a.id, event_id=a.event_id, title=a.title, source=a.source.name,
        source_id=a.source_id, url=a.canonical_url, content=a.summary, created_at=a.collected_at,
        published_at=a.published_at, status=a.event.decision if a.event else 'pending',
        score=a.event.score if a.event else 0)


@router.get('/articles')
def articles(q: str = '', source_id: int | None = None, status: Literal['pending', 'selected', 'skipped'] | None = None,
             since: datetime | None = None, until: datetime | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    with session_scope() as s:
        query = select(Article).outerjoin(Event)
        if q: query = query.where(Article.title.ilike(f'%{q}%'))
        if source_id: query = query.where(Article.source_id == source_id)
        if status: query = query.where(Event.decision == status)
        if since: query = query.where(Article.collected_at >= since)
        if until: query = query.where(Article.collected_at < until)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        return {'items': [article_json(a) for a in s.scalars(query.order_by(Article.id.desc()).offset((page-1)*page_size).limit(page_size))], 'total': total, 'page': page, 'page_size': page_size}


@router.get('/articles/{id}')
def article(id: int):
    with session_scope() as s: return article_json(require(s, Article, id))


class ArticleEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=1000)
    summary: str | None = Field(None, max_length=20000)


@router.patch('/articles/{id}')
def edit_article(id: int, payload: ArticleEdit):
    with session_scope() as s:
        a = require(s, Article, id)
        for name, value in payload.model_dump(exclude_none=True).items():
            setattr(a, name, value)
        return article_json(a)


@router.post('/articles/{id}/{action}')
def article_action(id: int, action: Literal['select', 'reject', 'generate-script']):
    with session_scope() as s:
        a = require(s, Article, id)
        event = s.scalar(select(Event).where(Event.id == a.event_id).with_for_update())
        if not event: raise HTTPException(409, 'Tin chưa được phân nhóm; hãy chạy xếp hạng trước')
        if action == 'generate-script':
            job = enqueue_script(s, CreateRequest(event_id=event.id, idempotency_key=str(uuid.uuid4())))
            return {'id': job.id, 'status': job.status}
        event.decision = 'selected' if action == 'select' else 'skipped'
        event.updated_at = datetime.now(timezone.utc)
        return article_json(a)


@router.get('/scripts')
def scripts(page: int = Query(1, ge=1)):
    with session_scope() as s:
        latest = select(func.max(ScriptVersion.id)).group_by(ScriptVersion.event_id)
        query = select(ScriptVersion).where(ScriptVersion.id.in_(latest))
        return {'items': [script_json(v) for v in s.scalars(query.order_by(ScriptVersion.id.desc()).offset((page-1)*25).limit(25))],
            'total': s.scalar(select(func.count()).select_from(query.subquery()))}


@router.get('/scripts/{id}')
def script(id: int):
    with session_scope() as s:
        v = require(s, ScriptVersion, id)
        result = script_json(v)
        job = s.get(ScriptJob, v.job_id) if v.job_id else None
        from app.models import ScriptSourceSnapshot
        snapshot = s.get(ScriptSourceSnapshot, v.snapshot_id)
        result['target_seconds'] = job.target_seconds if job else (snapshot.payload.get('target_seconds') if snapshot else None)
        return result


class ScriptEdit(BaseModel):
    data: dict


@router.patch('/scripts/{id}')
def edit_script(id: int, payload: ScriptEdit):
    with session_scope() as s:
        v = require(s, ScriptVersion, id)
        return script_json(save_version(s, v.event_id, v.id, payload.data))


@router.post('/scripts/{id}/regenerate')
def regenerate_script(id: int):
    with session_scope() as s:
        v = require(s, ScriptVersion, id)
        job = enqueue_script(s, CreateRequest(event_id=v.event_id, base_version_id=v.id, idempotency_key=str(uuid.uuid4())))
        return {'id': job.id, 'status': job.status}


@router.post('/scripts/{id}/approve')
@router.post('/scripts/{id}/reject')
def review_script(id: int, request: Request):
    with session_scope() as s:
        v = require(s, ScriptVersion, id)
        decision = 'approved' if request.url.path.endswith('/approve') else 'rejected'
        r = review_version(s, v.event_id, v.id, decision, 'dashboard', '')
        return {'id': r.id, 'decision': r.decision}


@router.get('/videos')
def videos(page: int = Query(1, ge=1)):
    with session_scope() as s:
        return {'items': [video_json(v) for v in s.scalars(select(VideoVersion).order_by(VideoVersion.id.desc()).offset((page-1)*25).limit(25))],
                'total': s.scalar(select(func.count()).select_from(VideoVersion))}


@router.get('/videos/{id}')
def video(id: int):
    with session_scope() as s: return video_json(require(s, VideoVersion, id))


@router.post('/videos/{id}/approve')
def approve_video(id: int):
    from app.routes.video import review, Review
    return review(id, Review(decision='approved', reviewer='dashboard'))


@router.post('/videos/{id}/render')
def render_video(id: int):
    from app.routes.video import create, CreateVideo
    with session_scope() as s:
        v = require(s, VideoVersion, id)
        payload = CreateVideo(script_version_id=v.script_version_id, parent_version_id=v.id,
                              idempotency_key=str(uuid.uuid4()), quality=v.config.get('quality', 'draft'), test_only=v.test_only)
    return create(payload)


@router.post('/scripts/{id}/render')
def render_script(id: int):
    from app.routes.video import create, CreateVideo
    return create(CreateVideo(script_version_id=id, idempotency_key=str(uuid.uuid4())))


class SceneEdit(BaseModel):
    narration: str | None = Field(None, min_length=1, max_length=10000)
    on_screen_text: str | None = Field(None, max_length=1000)
    visual_brief: str | None = Field(None, max_length=3000)
    seconds: int | None = Field(None, ge=1, le=90)


@router.patch('/scenes/{scene_id}')
def edit_scene(scene_id: str, payload: SceneEdit):
    # Scenes are embedded in immutable versions. Public IDs are version:scene.
    import copy
    try: version_id, number = map(int, scene_id.split(':'))
    except ValueError: raise HTTPException(422, 'Scene ID phải có dạng version:scene') from None
    with session_scope() as s:
        v = require(s, ScriptVersion, version_id)
        data = copy.deepcopy(v.data or {})
        scene = next((x for x in data.get('scenes', []) if x.get('scene_id') == number), None)
        if scene is None: raise HTTPException(404, 'Không tìm thấy scene')
        scene.update(payload.model_dump(exclude_none=True))
        if number == 1 and payload.narration is not None: data['hook'] = payload.narration
        return script_json(save_version(s, v.event_id, v.id, data))


@router.get('/operations/jobs')
def jobs(page: int = Query(1, ge=1), status: str = ''):
    with session_scope() as s:
        q = job_union()
        query = select(q)
        if status: query = query.where(q.c.status == status)
        return {'items': [dict(r) for r in s.execute(query.order_by(q.c.created_at.desc(), q.c.job_type, q.c.id.desc()).offset((page-1)*25).limit(25)).mappings()],
            'total': s.scalar(select(func.count()).select_from(query.subquery()))}


@router.get('/operations/jobs/{kind}/{id}')
def job_detail(kind: Literal['crawl', 'script', 'render', 'publish'], id: int):
    from app.llm.secrets import redact_secrets
    with session_scope() as s:
        j = require(s, JOB_MODELS[kind], id)
        return {'id': j.id, 'job_type': kind, 'status': j.status, 'created_at': j.created_at,
            'started_at': getattr(j, 'started_at', getattr(j, 'dispatched_at', None)),
            'finished_at': getattr(j, 'finished_at', None),
            'retry_count': max(0, getattr(j, 'attempts', 1) - 1) if kind != 'publish' else j.failures,
            'progress': getattr(j, 'progress', None), 'error_message': redact_secrets(j.error or '')}


@router.post('/operations/jobs/{kind}/{id}/{action}')
def job_action(kind: Literal['crawl', 'script', 'render', 'publish'], id: int, action: Literal['retry', 'cancel']):
    if kind == 'script':
        from app.routes.llm_control import retry_job, cancel_job, RetryInput
        return retry_job(id, RetryInput()) if action == 'retry' else cancel_job(id)
    if kind == 'render':
        from app.routes.video import retry, cancel
        return retry(id) if action == 'retry' else cancel(id)
    if kind == 'publish':
        from app.routes.publishing import action as publish_action
        # Reconcile an existing remote request; never issue a blind second upload.
        result = publish_action(id, 'reconcile' if action == 'retry' else action)
        return {k: result[k] for k in ('id', 'status')}
    if action == 'cancel': raise HTTPException(409, 'Worker crawl hiện chưa hỗ trợ hủy an toàn')
    from app.routes.web import retry_job
    retry_job(id)
    return {'id': id}


@router.get('/sources')
def sources():
    with session_scope() as s:
        return [{'id': r.id, 'name': r.name, 'enabled': r.enabled} for r in s.scalars(select(Source).order_by(Source.name))]


@router.post('/sources/{id}/crawl')
def crawl_source(id: int):
    from app.services.jobs import enqueue
    with session_scope() as s:
        require(s, Source, id)
        job = enqueue(s, source_id=id)
        return {'id': job.id, 'status': job.status}


@router.get('/publish')
def publishing():
    with session_scope() as s:
        return {'items': [{k: getattr(j, k) for k in ('id', 'video_version_id', 'status', 'created_at', 'post_ids')} for j in s.scalars(select(PublishJob).order_by(PublishJob.id.desc()).limit(100))]}


@router.get('/storage')
def storage():
    from app.services.video_service import DATA
    result = []
    for category in ('assets', 'audio', 'video', 'tmp'):
        folder = DATA / category
        files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink()] if folder.is_dir() else []
        result.append({'category': category, 'bytes': sum(p.stat().st_size for p in files), 'count': len(files),
            'files': [{'name': p.name, 'bytes': p.stat().st_size} for p in files[:100]]})
    return result


@router.post('/storage/cleanup')
def cleanup_storage():
    from datetime import timedelta
    from app.services.video_service import DATA
    import time
    with session_scope() as s:
        if s.scalar(select(VideoJob.id).where(VideoJob.status.in_(ACTIVE)).limit(1)):
            raise HTTPException(409, 'Chờ render hoàn tất trước khi dọn thư mục tạm')
        root = (DATA / 'tmp').resolve()
        removed = 0
        if root.is_dir():
            for path in root.iterdir():
                if path.is_symlink() or not path.is_file(): continue
                resolved = path.resolve()
                if resolved.parent == root and path.stat().st_mtime < time.time() - 86400:
                    path.unlink(); removed += 1
        return {'removed': removed}


@router.get('/settings')
def settings():
    from app.config import get_settings
    cfg = get_settings()
    return {'environment': cfg.app_env, 'provider': cfg.llm_provider, 'models': {'gemini': cfg.gemini_model,
        'deepseek': cfg.deepseek_model, 'groq': cfg.groq_model}, 'storage': 'local', 'poll_seconds': cfg.poll_seconds}
