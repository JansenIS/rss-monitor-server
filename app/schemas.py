from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class SourceOut(BaseModel):
    id: int
    country_code: str | None = None
    country_name: str | None = None
    source_name: str
    source_type: str | None = None
    homepage_url: str | None = None
    rss_url: str | None = None
    sitemap_url: str | None = None
    language: str | None = None
    priority: int
    reliability: int
    is_active: bool
    last_fetch_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_text: str | None = None
    last_http_status: int | None = None

    model_config = {'from_attributes': True}


class ArticleOut(BaseModel):
    id: int
    source_id: int | None = None
    country_code: str | None = None
    country_name: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    url: str
    canonical_url: str | None = None
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    author: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    content_hash: str | None = None
    url_hash: str

    model_config = {'from_attributes': True}


class ArticlesPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ArticleOut]


class SyncArticlesOut(BaseModel):
    server_time: datetime
    after_id: int
    next_after_id: int
    has_more: bool
    articles: list[ArticleOut]


class FetchRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    sources_total: int
    sources_ok: int
    sources_failed: int
    articles_new: int
    articles_duplicate: int
    duration_seconds: int | None = None
    error_text: str | None = None

    model_config = {'from_attributes': True}


class SourceLogOut(BaseModel):
    id: int
    run_id: int | None = None
    source_id: int | None = None
    source_name: str | None = None
    rss_url: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    http_status: int | None = None
    error_text: str | None = None
    articles_seen: int
    articles_new: int
    articles_duplicate: int

    model_config = {'from_attributes': True}


class ImportResult(BaseModel):
    sources_added: int = 0
    sources_updated: int = 0
    sources_skipped: int = 0
    articles_added: int = 0
    articles_duplicate: int = 0
    articles_skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    country_code: str | None = None


class StartRunResponse(BaseModel):
    command_id: int
    status: str
