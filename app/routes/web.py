import json
from datetime import datetime, time, timezone
from typing import Annotated
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from app.collectors.feeds import HTML_ADAPTERS
from app.collectors.normalize import canonical_url
from app.database import session_scope
from app.main import templates
from app.models import Article, Event, Job, Source, utcnow
from app.selection.ranking import lock_selection
from app.services.jobs import enqueue

router = APIRouter()
PAGE_SIZE = 30


def redirect(path):
    return RedirectResponse(path, status_code=303)


def get_or_404(session, model, item_id):
    item = session.get(model, item_id)
    if item is None:
        raise HTTPException(404, 'Không tìm thấy dữ liệu')
    return item


def render(request, name, **context):
    return templates.TemplateResponse(request=request, name=name, context=context)


def filters(q='', topic='', since='', until='', source_id=None):
    conditions = []
    if q.strip():
        term = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        conditions.append(or_(Article.title.ilike(term, escape='\\'), Article.summary.ilike(term, escape='\\')))
    if topic:
        conditions.append(Article.topic == topic)
    if source_id:
        conditions.append(Article.source_id == source_id)
    try:
        if since:
            conditions.append(func.coalesce(Article.published_at, Article.collected_at) >= datetime.combine(datetime.fromisoformat(since).date(), time.min, timezone.utc))
        if until:
            conditions.append(func.coalesce(Article.published_at, Article.collected_at) <= datetime.combine(datetime.fromisoformat(until).date(), time.max, timezone.utc))
    except ValueError:
        raise HTTPException(422, 'Ngày phải có dạng YYYY-MM-DD')
    return conditions


@router.get('/')
def home():
    return redirect('/selection')


@router.get('/sources')
def sources(request: Request, edit: int | None = None):
    with session_scope() as session:
        sources = list(session.scalars(select(Source).order_by(Source.id)))
        editing = get_or_404(session, Source, edit) if edit else None
        return render(request, 'sources.html', sources=sources, editing=editing, adapters=HTML_ADAPTERS)


@router.post('/sources/save')
def save_source(name: Annotated[str, Form(min_length=1, max_length=200)],
                url: Annotated[str, Form(min_length=1, max_length=2048)],
                kind: Annotated[str, Form()] = 'rss', topic: Annotated[str, Form(max_length=100)] = '',
                adapter: Annotated[str, Form()] = 'generic', interval_minutes: Annotated[int, Form(ge=1, le=10080)] = 30,
                source_id: Annotated[int | None, Form()] = None, enabled: Annotated[bool, Form()] = False):
    try:
        url = canonical_url(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not name.strip() or kind not in ('rss', 'html') or adapter not in HTML_ADAPTERS:
        raise HTTPException(422, 'Tên nguồn, loại nguồn hoặc adapter không hợp lệ')
    try:
        with session_scope() as session:
            source = get_or_404(session, Source, source_id) if source_id else Source(next_run_at=utcnow())
            source.name, source.url, source.kind, source.topic = name.strip(), url, kind, topic.strip()
            source.adapter, source.interval_minutes, source.enabled = adapter, interval_minutes, enabled
            session.add(source)
    except IntegrityError:
        raise HTTPException(409, 'URL nguồn đã tồn tại')
    return redirect('/sources')


@router.post('/sources/{source_id}/toggle')
def toggle_source(source_id: int):
    with session_scope() as session:
        source = get_or_404(session, Source, source_id)
        source.enabled = not source.enabled
        if source.enabled:
            source.next_run_at = utcnow()
    return redirect('/sources')


@router.post('/sources/{source_id}/collect')
def collect_source(source_id: int):
    with session_scope() as session:
        get_or_404(session, Source, source_id)
        job = enqueue(session, source_id)
        job_id = job.id
    return redirect(f'/jobs?highlight={job_id}')


@router.get('/articles')
def articles(request: Request, q: str = '', topic: str = '', since: str = '', until: str = '',
             source_id: str = '', page: Annotated[int, Query(ge=1)] = 1):
    try:
        source_filter = int(source_id) if source_id else None
    except ValueError:
        raise HTTPException(422, 'ID nguồn không hợp lệ')
    with session_scope() as session:
        conditions = filters(q, topic, since, until, source_filter)
        total = session.scalar(select(func.count()).select_from(Article).where(*conditions))
        items = list(session.scalars(select(Article).where(*conditions).options(joinedload(Article.source))
                                    .order_by(Article.collected_at.desc(), Article.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
        return render(request, 'articles.html', articles=items, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                      sources=list(session.scalars(select(Source).order_by(Source.name))),
                      topics=list(session.scalars(select(Article.topic).distinct().order_by(Article.topic))))


def events_page(request, decision, q, topic, since, until, page, selected=False):
    with session_scope() as session:
        conditions = [Event.articles.any()]
        if decision:
            if decision not in ('pending', 'selected', 'skipped'):
                raise HTTPException(422, 'Trạng thái duyệt không hợp lệ')
            conditions.append(Event.decision == decision)
        article_filters = filters(q, topic, since, until)
        if article_filters:
            conditions.append(exists(select(Article.id).where(Article.event_id == Event.id, *article_filters)))
        total = session.scalar(select(func.count()).select_from(Event).where(*conditions))
        events = list(session.scalars(select(Event).where(*conditions)
                                     .options(selectinload(Event.articles).joinedload(Article.source))
                                     .order_by(Event.score.desc(), Event.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
        return render(request, 'selection.html', events=events, selected=selected, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                      topics=list(session.scalars(select(Article.topic).distinct().order_by(Article.topic))))


@router.get('/selection')
def selection(request: Request, decision: str = 'pending', q: str = '', topic: str = '',
              since: str = '', until: str = '', page: Annotated[int, Query(ge=1)] = 1):
    return events_page(request, decision, q, topic, since, until, page)


@router.get('/selected')
def selected(request: Request, q: str = '', topic: str = '', since: str = '', until: str = '',
             page: Annotated[int, Query(ge=1)] = 1):
    return events_page(request, 'selected', q, topic, since, until, page, True)


@router.post('/selection/rank')
def rerank():
    with session_scope() as session:
        job = enqueue(session, kind='rank')
        job_id = job.id
    return redirect(f'/jobs?highlight={job_id}')


@router.post('/events/{event_id}/decision')
def decide(event_id: int, decision: Annotated[str, Form()], return_to: Annotated[str, Form()] = '/selection'):
    if decision not in ('pending', 'selected', 'skipped'):
        raise HTTPException(422, 'Quyết định không hợp lệ')
    with session_scope() as session:
        lock_selection(session)
        event = get_or_404(session, Event, event_id)
        event.decision, event.updated_at = decision, utcnow()
    return redirect(return_to if return_to in ('/selection', '/selected') else '/selection')


@router.post('/events/{event_id}/rename')
def rename_event(event_id: int, title: Annotated[str, Form(min_length=1, max_length=1000)]):
    if not title.strip():
        raise HTTPException(422, 'Tiêu đề không được để trống')
    with session_scope() as session:
        lock_selection(session)
        event = get_or_404(session, Event, event_id)
        event.title, event.manual_group = title.strip(), True
    return redirect('/selection?decision=')


@router.post('/articles/{article_id}/move')
def move_article(article_id: int, target_event_id: Annotated[int | None, Form()] = None):
    with session_scope() as session:
        lock_selection(session)
        article = get_or_404(session, Article, article_id)
        old = article.event
        if target_event_id:
            target = get_or_404(session, Event, target_event_id)
            if old and old.decision != target.decision:
                raise HTTPException(409, 'Hai nhóm có quyết định khác nhau. Hãy đưa về cùng trạng thái trước khi chuyển bài.')
        else:
            target = Event(title=article.title, decision=old.decision if old else 'pending', manual_group=True)
            session.add(target)
        if old:
            old.manual_group = True
        target.manual_group = True
        article.event = target
        session.flush()
        enqueue(session, kind='rank')
    return redirect('/selection?decision=')


@router.get('/selected/export.json')
def export_selected():
    with session_scope() as session:
        events = session.scalars(select(Event).where(Event.decision == 'selected', Event.articles.any())
                                 .options(selectinload(Event.articles).joinedload(Article.source)).order_by(Event.score.desc(), Event.id))
        data = {'schema_version': 1, 'exported_at': utcnow().isoformat(), 'events': [
            {'id': e.id, 'title': e.title, 'decision': e.decision, 'score': e.score,
             'components': e.components, 'reasons': e.reasons, 'articles': [
                 {'id': a.id, 'title': a.title, 'url': a.canonical_url, 'summary': a.summary, 'topic': a.topic,
                  'source': {'id': a.source.id, 'name': a.source.name, 'url': a.source.url},
                  'published_at': a.published_at.isoformat() if a.published_at else None,
                  'collected_at': a.collected_at.isoformat()} for a in sorted(e.articles, key=lambda a: a.id)]}
            for e in events]}
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="selected-news.json"'})


@router.get('/jobs')
def jobs(request: Request, page: Annotated[int, Query(ge=1)] = 1):
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(Job))
        items = list(session.scalars(select(Job).options(joinedload(Job.source)).order_by(Job.id.desc())
                                    .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
        return render(request, 'jobs.html', jobs=items, page=page, total=total,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))


@router.get('/api/jobs')
def job_status(ids: str = ''):
    try:
        job_ids = [int(value) for value in ids.split(',') if value][:PAGE_SIZE]
    except ValueError:
        raise HTTPException(422, 'Danh sách ID không hợp lệ')
    with session_scope() as session:
        query = select(Job).order_by(Job.id.desc()).limit(PAGE_SIZE)
        if job_ids:
            query = query.where(Job.id.in_(job_ids))
        return [dict(id=j.id, status=j.status, attempts=j.attempts, max_attempts=j.max_attempts,
                     new_count=j.new_count, duplicate_count=j.duplicate_count, error_count=j.error_count,
                     progress=j.progress, error=j.error, logs=j.logs) for j in session.scalars(query)]


@router.post('/jobs/{job_id}/retry')
def retry_job(job_id: int):
    with session_scope() as session:
        old = get_or_404(session, Job, job_id)
        if old.status != 'failed':
            raise HTTPException(409, 'Chỉ thử lại thủ công job đã thất bại')
        job = enqueue(session, old.source_id, old.kind)
        new_id = job.id
    return redirect(f'/jobs?highlight={new_id}')
