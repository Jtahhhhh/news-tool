from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from sqlalchemy import func, select
import pytest
from app.models import Article, Event, Job, Source, utcnow
from app.collectors.feeds import CollectedArticle
from app.services.jobs import claim_job, enqueue, execute_job, recover_expired, schedule_due
from app.selection.ranking import rank_events

pytestmark = pytest.mark.integration


def add_source(db, name='Example', url='https://example.com/feed', enabled=False):
    with db() as session:
        source = Source(name=name, url=url, enabled=enabled, topic='Science')
        session.add(source)
        session.flush()
        return source.id


def queue_claim(db, source_id=None, kind='collect'):
    with db() as session:
        enqueue(session, source_id, kind)
    with db() as session:
        return claim_job(session)


def sample():
    return CollectedArticle('https://example.com/news', 'Space agency launches new moon mission', 'Detailed summary ' * 10, utcnow(), 'a' * 64)


def test_dedup_replay_ranking_and_decisions(db, monkeypatch):
    source_id = add_source(db)
    item = sample()
    monkeypatch.setattr('app.services.jobs.collect', lambda source: ([item, item], 0))
    execute_job(*queue_claim(db, source_id))
    with db() as session:
        job = session.scalar(select(Job))
        assert (job.status, job.new_count, job.duplicate_count) == ('succeeded', 1, 1)
        event = session.scalar(select(Event))
        event.decision = 'selected'
        event_id = event.id
    execute_job(*queue_claim(db, source_id))
    execute_job(*queue_claim(db, kind='rank'))
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 1
        assert session.get(Event, event_id).decision == 'selected'
        session.get(Event, event_id).decision = 'skipped'
        rank_events(session)
        assert session.get(Event, event_id).decision == 'skipped'


def test_recovery_and_stale_owner_cannot_commit(db, monkeypatch):
    source_id = add_source(db)
    monkeypatch.setattr('app.services.jobs.collect', lambda source: ([sample()], 0))
    job_id, old_owner = queue_claim(db, source_id)
    with db() as session:
        session.get(Job, job_id).lease_until = utcnow() - timedelta(seconds=1)
    with db() as session:
        recover_expired(session)
        job = session.get(Job, job_id)
        assert job.status == 'retry'
        job.available_at = utcnow() - timedelta(seconds=1)
    with db() as session:
        new_claim = claim_job(session)
    execute_job(job_id, old_owner)
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
    execute_job(*new_claim)
    with db() as session:
        assert session.get(Job, job_id).status == 'succeeded'
        assert session.get(Job, job_id).attempts == 2


def test_no_overlap_and_concurrent_claim(db):
    source_id = add_source(db, enabled=True)
    with db() as session:
        first = enqueue(session, source_id).id
        assert enqueue(session, source_id).id == first
        schedule_due(session)
    def claim():
        with db() as session:
            return claim_job(session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(result is not None for result in results) == 1
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_cross_source_grouping_coverage_and_existing_decision(db, monkeypatch):
    first = add_source(db, 'First', 'https://first.example/feed')
    second = add_source(db, 'Second', 'https://second.example/feed')
    def collect(source):
        item = sample()
        item.canonical_url = f'https://example.com/article-{source.id}'
        return [item], 0
    monkeypatch.setattr('app.services.jobs.collect', collect)
    execute_job(*queue_claim(db, first))
    with db() as session:
        session.scalar(select(Event)).decision = 'skipped'
    execute_job(*queue_claim(db, second))
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        event = session.scalar(select(Event))
        assert len(event.articles) == 2 and event.components['coverage'] == 10
        assert event.decision == 'skipped'


def test_source_failure_does_not_block_others_and_retry_is_bounded(db, monkeypatch):
    bad = add_source(db, 'Bad', 'https://bad.example/feed')
    good = add_source(db, 'Good', 'https://good.example/feed')
    def collect(source):
        if source.id == bad:
            raise ValueError('Broken feed')
        return [sample()], 0
    monkeypatch.setattr('app.services.jobs.collect', collect)
    bad_claim = queue_claim(db, bad)
    execute_job(*bad_claim)
    execute_job(*queue_claim(db, good))
    with db() as session:
        assert session.scalar(select(Job).where(Job.source_id == good)).status == 'succeeded'
    for _ in range(2):
        with db() as session:
            session.get(Job, bad_claim[0]).available_at = utcnow() - timedelta(seconds=1)
        with db() as session:
            claim = claim_job(session)
        execute_job(*claim)
    with db() as session:
        job = session.get(Job, bad_claim[0])
        assert job.status == 'failed' and job.attempts == 3


def test_ui_end_to_end_export_and_manual_groups(db, client, monkeypatch):
    response = client.post('/sources/save', data={'name': 'Example', 'url': 'https://example.com/feed', 'topic': 'Science'})
    assert response.status_code == 200
    assert client.post('/sources/1/collect').status_code == 200
    monkeypatch.setattr('app.services.jobs.collect', lambda source: ([sample()], 0))
    with db() as session:
        claim = claim_job(session)
    execute_job(*claim)
    for path in ('/health', '/sources', '/articles', '/selection', '/selected', '/jobs', '/api/jobs'):
        assert client.get(path).status_code == 200, path
    assert 'Space agency' in client.get('/articles?q=Space').text
    assert 'Space agency' not in client.get('/articles?topic=Economy').text
    assert client.post('/events/1/decision', data={'decision': 'selected'}).status_code == 200
    export = client.get('/selected/export.json').json()
    assert len(export['events']) == 1 and export['events'][0]['articles'][0]['source']['name'] == 'Example'
    assert client.post('/articles/1/move', data={}).status_code == 200
    with db() as session:
        article = session.get(Article, 1)
        new_id = article.event_id
        assert new_id != 1 and article.event.decision == 'selected' and article.event.manual_group
        rank_events(session)
        assert article.event_id == new_id
    assert client.post(f'/events/{new_id}/decision', data={'decision': 'pending'}).status_code == 200
    assert client.get('/selected/export.json').json()['events'] == []


def test_csrf_and_duplicate_source(db, client):
    data = {'name': 'Example', 'url': 'https://example.com/feed'}
    assert client.post('/sources/save', data=data).status_code == 200
    assert client.post('/sources/save', data=data).status_code == 409
    client.headers.pop('x-csrf-token')
    assert client.post('/sources/1/collect').status_code == 403
    assert client.post('/sources/1/collect', data={'csrf_token': client.cookies['csrf_token']}).status_code == 200
