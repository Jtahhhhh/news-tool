from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from sqlalchemy import URL
from sqlalchemy.engine import make_url
from pydantic import model_validator
import os


def postgres_url(value):
    try:
        url = make_url(value)
        if url.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg') or not url.host or not url.database:
            raise ValueError()
        return url.set(drivername='postgresql+psycopg')
    except Exception:
        raise ValueError('Invalid PostgreSQL connection configuration') from None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', hide_input_in_errors=True)
    app_env: str = 'local'
    debug: bool = False
    db_host: str = 'localhost'
    db_port: int = 5432
    postgres_db: str = 'news_tool'
    postgres_user: str = 'news_tool'
    postgres_password: str = Field('change-this-local-password', repr=False)
    database_url: str | None = Field(None, repr=False)
    migration_database_url: str | None = Field(None, repr=False)
    db_pool_mode: str = 'auto'
    db_pool_size: int = Field(5, ge=1, le=50)
    db_max_overflow: int = Field(5, ge=0, le=50)
    db_pool_recycle: int = Field(300, ge=30, le=3600)
    db_connect_timeout: int = Field(10, ge=1, le=60)
    db_pool_timeout: int = Field(15, ge=1, le=120)

    @model_validator(mode='after')
    def database_configuration(self):
        if self.app_env not in ('local','test','production'):
            raise ValueError('APP_ENV must be local, test or production')
        if self.app_env == 'production' and (not self.database_url or self.debug):
            raise ValueError('Production requires DATABASE_URL and DEBUG=false')
        if self.db_pool_mode not in ('auto','direct','pgbouncer'):
            raise ValueError('DB_POOL_MODE must be auto, direct or pgbouncer')
        if self.database_url: postgres_url(self.database_url)
        if self.db_pool_mode=='direct' and self.db_url.port==6432:
            raise ValueError('Port 6432 requires PgBouncer mode')
        if self.migration_database_url:
            if postgres_url(self.migration_database_url).port == 6432:
                raise ValueError('MIGRATION_DATABASE_URL must use a direct connection')
            if self.migration_url.database != self.db_url.database:
                raise ValueError('Application and migration URLs must select the same database')
        if self.uses_pgbouncer and not self.migration_database_url:
            raise ValueError('PgBouncer requires a direct MIGRATION_DATABASE_URL')
        return self

    @property
    def uses_pgbouncer(self):
        return self.db_pool_mode == 'pgbouncer' or (self.db_pool_mode == 'auto' and self.db_url.port == 6432)

    @property
    def migration_url(self):
        return postgres_url(self.migration_database_url) if self.migration_database_url else self.db_url
    poll_seconds: float = Field(default=3, ge=0.1, le=60)
    job_timeout_seconds: int = Field(default=120, ge=5, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600)
    request_timeout_seconds: int = Field(default=20, ge=1, le=120)
    max_response_bytes: int = Field(default=5_242_880, ge=1024)
    max_articles_per_job: int = Field(default=150, ge=1, le=1000)
    rank_keywords: str = ''
    allow_private_sources: bool = False
    llm_provider: str = 'gemini'
    gemini_model: str = 'gemini-3.8-flash'
    deepseek_model: str = 'deepseek-flash'
    gemini_api_key: str = ''
    deepseek_api_key: str = ''
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_max_output_tokens: int = Field(default=8192, ge=256, le=32768)
    llm_allow_fake: bool = False
    groq_model: str = 'openai/gpt-oss-120b'
    groq_api_key: str = ''
    llm_fallback_provider: str = ''
    llm_max_concurrency: int = Field(default=2, ge=1, le=20)
    llm_fetch_article: bool = True
    article_fetch_timeout_seconds: int = Field(default=3, ge=1, le=10)
    worker_role: str = 'combined'
    max_media_upload_mb: int = Field(default=500, ge=1, le=4096)

    @property
    def db_url(self):
        return postgres_url(self.database_url) if self.database_url else URL.create(
            'postgresql+psycopg', username=self.postgres_user,
            password=self.postgres_password, host=self.db_host,
            port=self.db_port, database=self.postgres_db,
        )


@lru_cache
def get_settings():
    # Production must never silently inherit a developer's local .env.
    return Settings(_env_file=None) if os.getenv('APP_ENV') == 'production' else Settings()
