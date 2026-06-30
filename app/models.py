from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, BigInteger, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = 'sources'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    country_code: Mapped[str | None] = mapped_column(String(16), index=True)
    country_name: Mapped[str | None] = mapped_column(String(255))
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(128), index=True)
    homepage_url: Mapped[str | None] = mapped_column(Text)
    rss_url: Mapped[str | None] = mapped_column(Text, index=True)
    sitemap_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(64), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    reliability: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_text: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    articles = relationship('Article', back_populates='source')

    __table_args__ = (
        Index('idx_sources_country_active', 'country_code', 'is_active'),
    )


class Article(Base):
    __tablename__ = 'articles'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('sources.id', ondelete='SET NULL'), index=True)

    country_code: Mapped[str | None] = mapped_column(String(16), index=True)
    country_name: Mapped[str | None] = mapped_column(String(255))
    source_name: Mapped[str | None] = mapped_column(String(500), index=True)
    source_type: Mapped[str | None] = mapped_column(String(128), index=True)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)

    author: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    content_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    url_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    publishing_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    publishing_job_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    source = relationship('Source', back_populates='articles')

    __table_args__ = (
        Index('idx_articles_country_published', 'country_code', 'published_at'),
        Index('idx_articles_country_fetched', 'country_code', 'fetched_at'),
    )


class FetchRun(Base):
    __tablename__ = 'fetch_runs'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64), default='running', index=True)

    sources_total: Mapped[int] = mapped_column(Integer, default=0)
    sources_ok: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, default=0)
    articles_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    error_text: Mapped[str | None] = mapped_column(Text)


class FetchSourceLog(Base):
    __tablename__ = 'fetch_source_logs'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('fetch_runs.id', ondelete='CASCADE'), index=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('sources.id', ondelete='SET NULL'), index=True)

    source_name: Mapped[str | None] = mapped_column(String(500))
    rss_url: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(64), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_text: Mapped[str | None] = mapped_column(Text)

    articles_seen: Mapped[int] = mapped_column(Integer, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, default=0)
    articles_duplicate: Mapped[int] = mapped_column(Integer, default=0)


class FetchCommand(Base):
    __tablename__ = 'fetch_commands'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    command_type: Mapped[str] = mapped_column(String(64), default='full_fetch', index=True)
    status: Mapped[str] = mapped_column(String(64), default='queued', index=True)
    country_code: Mapped[str | None] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('fetch_runs.id', ondelete='SET NULL'))
    error_text: Mapped[str | None] = mapped_column(Text)

class PublishingSettings(Base):
    __tablename__ = 'publishing_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    routerai_api_key: Mapped[str | None] = mapped_column(Text)
    routerai_base_url: Mapped[str] = mapped_column(Text, default='https://routerai.ru/api/v1')
    rewrite_model: Mapped[str | None] = mapped_column(String(255))
    image_model: Mapped[str | None] = mapped_column(String(255))
    default_language: Mapped[str] = mapped_column(String(64), default='ru')
    stop_words: Mapped[str | None] = mapped_column(Text)
    specificity: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WordPressSite(Base):
    __tablename__ = 'wordpress_sites'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    app_password: Mapped[str | None] = mapped_column(Text)
    default_status: Mapped[str] = mapped_column(String(64), default='draft')
    categories: Mapped[list | None] = mapped_column(JSONB)
    language: Mapped[str | None] = mapped_column(String(64))
    specificity: Mapped[str | None] = mapped_column(Text)
    generation_limit_per_hour: Mapped[int | None] = mapped_column(Integer)
    generation_limit_per_24h: Mapped[int | None] = mapped_column(Integer)
    generation_limit_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PublishJob(Base):
    __tablename__ = 'publish_jobs'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(64), default='queued', index=True)
    country_code: Mapped[str] = mapped_column(String(16), index=True)
    country_name: Mapped[str | None] = mapped_column(String(255))
    target_language: Mapped[str] = mapped_column(String(64), default='ru')
    hours_back: Mapped[int] = mapped_column(Integer, default=1)
    pipeline_type: Mapped[str] = mapped_column(String(64), default='recent', index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    articles_per_day: Mapped[int | None] = mapped_column(Integer)
    planned_articles_per_site: Mapped[int | None] = mapped_column(Integer)
    site_limit: Mapped[int | None] = mapped_column(Integer)
    rewrite_model: Mapped[str | None] = mapped_column(String(255))
    image_model: Mapped[str | None] = mapped_column(String(255))
    stop_words: Mapped[str | None] = mapped_column(Text)
    specificity: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_text: Mapped[str | None] = mapped_column(Text)
    articles_snapshot: Mapped[dict | list | None] = mapped_column(JSONB)


class PublishedArticle(Base):
    __tablename__ = 'published_articles'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('publish_jobs.id', ondelete='SET NULL'), index=True)
    site_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('wordpress_sites.id', ondelete='SET NULL'), index=True)
    status: Mapped[str] = mapped_column(String(64), default='queued', index=True)
    title: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    seo_title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    image_prompt: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    wp_post_id: Mapped[int | None] = mapped_column(BigInteger)
    wp_media_id: Mapped[int | None] = mapped_column(BigInteger)
    wp_link: Mapped[str | None] = mapped_column(Text)
    category_ids: Mapped[list | None] = mapped_column(JSONB)
    source_article_ids: Mapped[list | None] = mapped_column(JSONB)
    sequence_number: Mapped[int | None] = mapped_column(Integer)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
