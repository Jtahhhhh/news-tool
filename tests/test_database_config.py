import pytest
from sqlalchemy.pool import NullPool, QueuePool
from app.config import Settings, get_settings
from app.database import create_db_engine


def settings(**kw):
    return Settings(_env_file=None,**kw)


@pytest.mark.parametrize('scheme',['postgres','postgresql','postgresql+psycopg'])
def test_render_url_uses_installed_driver(scheme):
    s=settings(database_url=f'{scheme}://user:dummy@internal:5432/news?sslmode=require')
    assert s.db_url.drivername=='postgresql+psycopg'
    assert s.db_url.query['sslmode']=='require'
    assert 'dummy' not in repr(s)


def test_pool_requires_direct_migration_connection():
    with pytest.raises(ValueError,match='MIGRATION_DATABASE_URL'):
        settings(database_url='postgresql://user:dummy@internal:6432/news')
    s=settings(database_url='postgresql://user:dummy@internal:6432/news',
               migration_database_url='postgresql://user:dummy@internal:5432/news')
    e=create_db_engine(s);m=create_db_engine(s,migration=True)
    assert isinstance(e.pool,NullPool) and isinstance(m.pool,NullPool)
    assert e.url.port==6432 and m.url.port==5432
    e.dispose();m.dispose()


def test_direct_local_pool():
    s=settings(database_url='postgresql://user:dummy@localhost:5432/local')
    e=create_db_engine(s)
    assert isinstance(e.pool,QueuePool) and e.pool.size()==5
    e.dispose()


def test_production_ignores_dotenv(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'.env').write_text('DATABASE_URL=postgresql://local:dummy@localhost/local\n')
    monkeypatch.setenv('APP_ENV','production');monkeypatch.delenv('DATABASE_URL',raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError,match='Production requires'): get_settings()
        monkeypatch.setenv('DATABASE_URL','postgresql://prod:dummy@internal/news')
        assert get_settings().db_url.host=='internal'
    finally: get_settings.cache_clear()


def test_reject_debug_and_invalid_url_without_secret():
    with pytest.raises(ValueError,match='DEBUG=false'):
        settings(app_env='production',debug=True,database_url='postgresql://u:dummy@host/db')
    with pytest.raises(ValueError) as error: settings(database_url='not-a-url-secret-sentinel')
    assert 'secret-sentinel' not in str(error.value)


@pytest.mark.parametrize('app_env', [None, 'local', 'test', 'production'])
def test_render_never_falls_back_to_local_dotenv(tmp_path, monkeypatch, app_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.env').write_text('DATABASE_URL=postgresql://u:secret@localhost/db\n')
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('APP_ENV', raising=False)
    if app_env is not None:
        monkeypatch.setenv('APP_ENV', app_env)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match='APP_ENV=production|DATABASE_URL is not configured'):
            get_settings()
        if app_env in (None, 'production'):
            monkeypatch.setenv('DATABASE_URL', 'postgres://u:secret@internal/db')
            assert get_settings().app_env == 'production'
            assert get_settings().db_url.host == 'internal'
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('host', ['localhost', 'LOCALHOST.', '127.0.0.1', '127.0.0.2', '[::1]'])
@pytest.mark.parametrize('field', ['database_url', 'migration_database_url'])
def test_production_rejects_loopback(host, field):
    values = dict(app_env='production', database_url='postgres://u:secret@internal/db')
    values[field] = f'postgres://u:secret@{host}/db'
    with pytest.raises(ValueError, match='loopback'):
        settings(**values)


def test_production_diagnostic_hides_connection(capsys):
    s = settings(app_env='production', database_url='postgres://private-user:secret@private-host/db')
    for migration in (False, True):
        engine = create_db_engine(s, migration=migration)
        engine.dispose()
    output = capsys.readouterr().out
    assert 'DATABASE_URL_PRESENT=true' in output
    assert 'ROLE=migration' in output and 'ROLE=application' in output
    assert 'DB_PORT=5432' in output
    for value in ('private-user', 'secret', 'private-host', 'postgres://'):
        assert value not in output


def test_transaction_rollback_reconnect_and_revision(db):
    from sqlalchemy import text
    from app.database import engine
    from app.models import Event
    from app.start import check_database
    check_database()
    with pytest.raises(RuntimeError,match='rollback test'):
        with db() as s:
            row=Event(title='rollback test');s.add(row);s.flush();rolled_back=row.id
            raise RuntimeError('rollback test')
    with db() as s: assert s.get(Event,rolled_back) is None
    with db() as s:
        row=Event(title='persist test');s.add(row);s.flush();saved=row.id
    engine.dispose()
    with db() as s:
        row=s.get(Event,saved);assert row.title=='persist test';row.title='updated'
    engine.dispose()
    with db() as s:
        row=s.get(Event,saved);assert row.title=='updated';s.delete(row)
