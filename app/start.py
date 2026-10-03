import os
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.database import engine, create_db_engine
from app.config import get_settings
from alembic.script import ScriptDirectory


def check_database():
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
        actual=set(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
        expected=set(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())
        if actual != expected: raise RuntimeError('Database migration revision mismatch')
    print('Database connection: OK', flush=True)


def migrate():
    # All web replicas serialize schema changes on one PostgreSQL connection.
    migration_engine=create_db_engine(get_settings(),migration=True)
    with migration_engine.connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(721001)'))
        connection.commit()
        try:
            config = Config('alembic.ini')
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(721001)'))
            connection.commit()
    migration_engine.dispose()


if __name__ == '__main__':
    try:
        migrate()
        check_database()
    except Exception:
        # Connection/driver exceptions may contain a URL, username or password.
        print('Database startup: FAILED. Check connection settings and migrations.',flush=True)
        raise SystemExit(1) from None
    # OAuth callback query strings contain one-time authorization codes.
    os.execvp('uvicorn', ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', os.getenv('PORT','8000'), '--no-access-log'])
