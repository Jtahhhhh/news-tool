import logging
import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.config import get_settings
from app.database import session_scope
from app.models import Article, Job, Source, utcnow
from app.collectors import collect
from app.selection.ranking import rank_events

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running', 'retry')


def log_job(job, message):
    job.logs = [*job.logs, {'at': utcnow().isoformat(), 'attempt': job.attempts, 'message': message}][-50:]
    job.progress = message
    logger.info('job=%s source=%s attempt=%s %s', job.id, job.source_id, job.attempts, message)


def enqueue(session, source_id=None, kind='collect'):
    result = session.execute(insert(Job).values(source_id=source_id, kind=kind,
                              max_attempts=get_settings().max_attempts).on_conflict_do_nothing().returning(Job.id)).scalar()
    if result is not None:
        return session.get(Job, result)
    condition = Job.source_id == source_id if kind == 'collect' else Job.kind == 'rank'
    return session.scalar(select(Job).where(condition, Job.status.in_(ACTIVE)).order_by(Job.id))


def schedule_due(session):
    now = utcnow()
    sources = session.scalars(select(Source).where(Source.enabled.is_(True), Source.next_run_at <= now)
                              .order_by(Source.next_run_at).with_for_update(skip_locked=True))
    for source in sources:
        enqueue(session, source.id)
        source.next_run_at = now + timedelta(minutes=source.interval_minutes)


def retry_or_fail(job, message):
    job.error = message[:4000]
    job.error_count += 1
    job.owner = None
    job.lease_until = None
    if job.attempts < job.max_attempts:
        job.status = 'retry'
        job.available_at = utcnow() + timedelta(seconds=get_settings().retry_delay_seconds * max(1, job.attempts))
        log_job(job, f'Sẽ thử lại: {message}')
    else:
        job.status = 'failed'
        job.finished_at = utcnow()
        log_job(job, f'Đã hết số lần thử: {message}')


def recover_expired(session):
    for job in session.scalars(select(Job).where(Job.status == 'running', Job.lease_until < utcnow())
                               .with_for_update(skip_locked=True)):
        retry_or_fail(job, 'Worker mất kết nối hoặc job quá thời hạn; phục hồi yêu cầu')


def claim_job(session):
    job = session.scalar(select(Job).where(Job.status.in_(('queued', 'retry')), Job.available_at <= utcnow())
                         .order_by(Job.available_at, Job.id).with_for_update(skip_locked=True).limit(1))
    if not job:
        return None
    job.status = 'running'
    job.owner = uuid.uuid4().hex
    job.attempts += 1
    job.started_at = utcnow()
    job.lease_until = utcnow() + timedelta(seconds=get_settings().job_timeout_seconds)
    log_job(job, 'Đang tải nguồn' if job.kind == 'collect' else 'Đang gom nhóm và chấm điểm')
    session.flush()
    return job.id, job.owner


def fail_owned(job_id, owner, message):
    with session_scope() as session:
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job and job.status == 'running' and job.owner == owner:
            retry_or_fail(job, message)


def persist_articles(session, source, articles):
    new_count = duplicate_count = 0
    # Consistent insertion order reduces unique-index deadlocks across overlapping feeds.
    for article in sorted(articles, key=lambda item: item.canonical_url):
        result = session.execute(insert(Article).values(
            source_id=source.id, canonical_url=article.canonical_url, title=article.title,
            summary=article.summary, published_at=article.published_at, fingerprint=article.fingerprint,
            topic=source.topic,
        ).on_conflict_do_nothing().returning(Article.id)).scalar()
        if result is None:
            duplicate_count += 1
        else:
            new_count += 1
    return new_count, duplicate_count


def execute_job(job_id, owner):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if not job or job.owner != owner or job.status != 'running':
                return
            kind = job.kind
            source = session.get(Source, job.source_id) if job.source_id else None
        articles, errors = collect(source) if kind == 'collect' else ([], 0)
        with session_scope() as session:
            # Fence stale processes before writing. Lock lasts until data + job commit together.
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.owner != owner or job.status != 'running' or job.lease_until <= utcnow():
                return
            if kind == 'collect':
                job.new_count, job.duplicate_count = persist_articles(session, source, articles)
                job.error_count += errors
            count = rank_events(session)
            job.status = 'succeeded'
            job.finished_at = utcnow()
            job.owner = None
            job.lease_until = None
            log_job(job, f'Hoàn tất: {job.new_count} mới, {job.duplicate_count} trùng, {errors} bài lỗi; {count} nhóm được chấm điểm')
    except Exception as exc:
        logger.exception('Job %s failed', job_id)
        fail_owned(job_id, owner, f'{type(exc).__name__}: {exc}')
