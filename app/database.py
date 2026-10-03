from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    pass


def create_db_engine(settings, *, migration=False):
    options = dict(pool_pre_ping=True, hide_parameters=True,
                   connect_args={'connect_timeout': settings.db_connect_timeout})
    if migration or settings.uses_pgbouncer:
        options['poolclass'] = NullPool
        options['connect_args']['prepare_threshold'] = None
    else:
        options.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                       pool_recycle=settings.db_pool_recycle, pool_timeout=settings.db_pool_timeout)
    return create_engine(settings.migration_url if migration else settings.db_url, **options)


engine = create_db_engine(get_settings())
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope():
    with SessionLocal() as session:
        with session.begin():
            yield session
