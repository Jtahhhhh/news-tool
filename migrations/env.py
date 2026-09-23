from alembic import context
from app.database import engine, Base
import app.models  # noqa: F401


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError('Use online migrations with PostgreSQL')
elif context.config.attributes.get('connection') is not None:
    run(context.config.attributes['connection'])
else:
    with engine.connect() as connection:
        run(connection)
