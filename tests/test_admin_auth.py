from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import dashboard_auth as auth
from app.models import AdminUser, AdminSession, utcnow


@pytest.fixture
def auth_store(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'auth.db'), connect_args={'check_same_thread': False})
    AdminUser.__table__.create(engine)
    AdminSession.__table__.create(engine)
    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as db, db.begin():
            yield db
    settings = SimpleNamespace(admin_username='admin', admin_password='correct-password',
                               auth_secret_key='test-signing-key-with-at-least-32-characters', app_env='test', session_secure=False)
    monkeypatch.setattr(auth, 'session_scope', scope)
    monkeypatch.setattr(auth, 'get_settings', lambda: settings)
    auth.attempts.clear()
    yield scope, settings
    engine.dispose()


def test_login_protection_logout_and_restart(auth_store):
    scope, settings = auth_store
    from app.main import app
    with TestClient(app) as c:
        assert c.get('/login').status_code == 200
        for path in ('/', '/news', '/scripts', '/videos', '/editor', '/publish', '/settings', '/dashboard/', '/tiktok/callback'):
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers['location'] == '/login'
        for path in ('/api/settings', '/api/articles', '/script-jobs/1'):
            assert c.get(path).status_code == 401
            assert c.post(path).status_code == 401
        assert c.post('/login', data={'username':'admin','password':'correct-password'}).status_code == 403
        c.headers['x-csrf-token'] = c.cookies['csrf_token']
        assert c.post('/login', data={'username':'admin','password':'wrong'}).status_code == 401
        result = c.post('/login', data={'username':'admin','password':'correct-password'}, follow_redirects=False)
        assert result.status_code == 303 and result.headers['location'] == '/'
        assert 'HttpOnly' in result.headers['set-cookie'] and 'SameSite=lax' in result.headers['set-cookie']
        token = c.cookies[auth.COOKIE]
        assert c.get('/api/auth/session').json()['authenticated']
        with scope() as db:
            admin = db.get(AdminUser, 1)
            assert admin.password_hash != settings.admin_password
            assert auth.password_matches(settings.admin_password, admin.password_hash)
            assert admin.last_login_at is not None
        settings.admin_password = ''
        auth.bootstrap_admin()
        assert auth.valid_session(token)
        assert c.post('/logout', follow_redirects=False).status_code == 303
        assert not auth.valid_session(token)
        c.cookies.set(auth.COOKIE, token)
        assert c.get('/api/settings').status_code == 401
    with TestClient(app) as c:
        assert c.get('/login').status_code == 200
    assert not any('register' in path for path in app.openapi()['paths'])


def test_expiry_tampering_secure_cookie_and_no_reset(auth_store):
    scope, settings = auth_store
    from app.main import app
    settings.session_secure = True
    with TestClient(app, base_url='https://testserver') as c:
        c.get('/login')
        c.headers['x-csrf-token'] = c.cookies['csrf_token']
        result = c.post('/api/auth/login', json={'username':'admin','password':'correct-password'})
        assert result.status_code == 200 and 'Secure' in result.headers['set-cookie']
        token = c.cookies[auth.COOKIE]
        assert not auth.valid_session(token + 'x')
        settings.admin_password = 'changed-env-password'
        auth.bootstrap_admin()
        with scope() as db:
            assert auth.password_matches('correct-password', db.get(AdminUser, 1).password_hash)
            db.get(AdminSession, auth.token_hash(token)).expires_at = utcnow() - timedelta(seconds=1)
        assert not auth.valid_session(token)


def test_bootstrap_requires_config(auth_store):
    _, settings = auth_store
    settings.admin_password = ''
    with pytest.raises(RuntimeError, match='Initial startup'):
        auth.bootstrap_admin()
    settings.auth_secret_key = ''
    with pytest.raises(RuntimeError, match='AUTH_SECRET_KEY'):
        auth.bootstrap_admin()
