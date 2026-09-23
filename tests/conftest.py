import os
import pytest

if os.environ.get('TEST_DATABASE_URL'):
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']


@pytest.fixture
def db():
    if not os.environ.get('TEST_DATABASE_URL'):
        pytest.skip('Set TEST_DATABASE_URL to a separate PostgreSQL database ending in _test')
    from sqlalchemy import text
    from app.database import engine, session_scope
    from app.start import migrate
    if not engine.url.database.endswith('_test'):
        pytest.fail('Integration tests only run against a database ending in _test')
    migrate()
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE llm_attempts, llm_credentials, llm_quota_states, llm_provider_health, llm_policy, jobs, articles, events, sources RESTART IDENTITY CASCADE'))
    yield session_scope


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        client.get('/sources')
        client.headers['x-csrf-token'] = client.cookies['csrf_token']
        yield client
