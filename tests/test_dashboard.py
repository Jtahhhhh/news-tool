import hashlib
import time
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app import dashboard_auth as auth
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def test_local_storage_roundtrip_and_traversal(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'example')
    storage = LocalStorage(tmp_path / 'objects')
    storage.upload(source, 'video/demo.mp4')
    assert storage.exists('video/demo.mp4')
    out = tmp_path / 'out'
    storage.download('video/demo.mp4', out)
    assert out.read_bytes() == b'example'
    for key in ('../secret', '/absolute', 'x/../../secret', 'x\\secret', ''):
        with pytest.raises(ValueError): storage.exists(key)
    storage.delete('video/demo.mp4')
    assert not storage.exists('video/demo.mp4')


def test_s3_missing_is_distinct_from_access_denied():
    client = Mock()
    storage = S3Storage(client, 'private', 'news')
    storage.get_signed_url('video/one.mp4', 60)
    client.generate_presigned_url.assert_called_once_with('get_object', Params={'Bucket': 'private', 'Key': 'news/video/one.mp4'}, ExpiresIn=60)
    error = RuntimeError('denied')
    error.response = {'Error': {'Code': '403'}}
    client.head_object.side_effect = error
    with pytest.raises(RuntimeError): storage.exists('one')
    error.response = {'Error': {'Code': '404'}}
    assert not storage.exists('one')


def test_aggregate_counts_and_pagination(client, db):
    from app.models import Source, Article, Event, Job
    with db() as s:
        source = Source(name='Dashboard test', url='https://example.com/feed')
        event = Event(title='Story', decision='selected')
        s.add_all([source, event]); s.flush()
        for i in range(27):
            s.add(Article(source_id=source.id, event_id=event.id, title=f'Story {i}', canonical_url=f'https://example.com/{i}', fingerprint=str(i)))
        s.add(Job(kind='collect', source_id=source.id, status='failed', error='private details'))
    summary = client.get('/api/dashboard/summary').json()
    assert summary['articles_today'] == 27
    assert summary['failed_jobs'] == 1
    result = client.get('/api/articles?page=2').json()
    assert result['total'] == 27 and len(result['items']) == 2
    assert client.get('/api/articles?page=0').status_code == 422
    jobs = client.get('/api/operations/jobs').json()['items']
    assert jobs[0]['job_type'] == 'crawl'
    assert 'private details' not in str(jobs)
