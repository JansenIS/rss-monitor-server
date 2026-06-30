import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def _ensure_publishing_columns() -> None:
    """Best-effort additive schema upgrades for installs created before publishing jobs grew."""
    statements = [
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS pipeline_type VARCHAR(64) DEFAULT 'recent'",
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS period_start TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS period_end TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS articles_per_day INTEGER",
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS planned_articles_per_site INTEGER",
        "ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS retry_after TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS country_codes JSONB",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS generation_limit_per_hour INTEGER",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS generation_limit_per_24h INTEGER",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS generation_limit_reset_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE published_articles ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE published_articles ADD COLUMN IF NOT EXISTS sequence_number INTEGER",
        "ALTER TABLE published_articles ADD COLUMN IF NOT EXISTS source_article_ids JSONB",
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS publishing_used_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS publishing_job_id BIGINT",
    ]
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    except Exception as exc:
        print(f"[db] publishing schema upgrade skipped: {exc}", flush=True)


def init_db() -> None:
    from . import models  # noqa: F401

    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_publishing_columns()
            return
        except OperationalError as exc:
            last_error = exc
            print(f"[db] PostgreSQL is not ready yet, attempt {attempt}/30: {exc}", flush=True)
            time.sleep(2)

    raise RuntimeError(f"PostgreSQL did not become available after 60 seconds: {last_error}")
