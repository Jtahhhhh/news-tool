import os
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.database import engine


def migrate():
    # All web replicas serialize schema changes on one PostgreSQL connection.
    with engine.connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(721001)'))
        connection.commit()
        try:
            config = Config('alembic.ini')
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(721001)'))
            connection.commit()


if __name__ == '__main__':
    migrate()
    # OAuth callback query strings contain one-time authorization codes.
    os.execvp('uvicorn', ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log'])
