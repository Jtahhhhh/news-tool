from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    db_host: str = 'localhost'
    db_port: int = 5432
    postgres_db: str = 'news_tool'
    postgres_user: str = 'news_tool'
    postgres_password: str = 'change-this-local-password'
    database_url: str | None = None
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

    @property
    def db_url(self):
        return self.database_url or URL.create(
            'postgresql+psycopg', username=self.postgres_user,
            password=self.postgres_password, host=self.db_host,
            port=self.db_port, database=self.postgres_db,
        )


@lru_cache
def get_settings():
    return Settings()
