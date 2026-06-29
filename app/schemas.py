from __future__ import annotations
from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator


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
    publishing_used_at: datetime | None = None
    publishing_job_id: int | None = None

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

class PublishingSettingsIn(BaseModel):
    routerai_api_key: str | None = None
    routerai_base_url: str | None = None
    rewrite_model: str | None = None
    image_model: str | None = None
    default_language: str = 'en'
    stop_words: str | None = None
    specificity: str | None = None


class PublishingSettingsOut(PublishingSettingsIn):
    id: int = 1
    routerai_api_key_saved: bool = False
    routerai_api_key: str | None = None
    model_config = {'from_attributes': True}


class WordPressCategory(BaseModel):
    id: int
    name: str


class WordPressSiteIn(BaseModel):
    name: str
    base_url: str
    username: str | None = None
    app_password: str | None = None
    default_status: str = 'draft'
    categories: list[WordPressCategory] = Field(default_factory=list)
    language: str | None = None
    specificity: str | None = None
    generation_limit_per_hour: int | None = Field(default=None, ge=1)
    generation_limit_per_24h: int | None = Field(default=None, ge=1)
    is_active: bool = True

    @field_validator('categories', mode='before')
    @classmethod
    def normalize_categories(cls, value):
        if value is None:
            return []
        normalized = []
        for item in value:
            if isinstance(item, int):
                normalized.append({'id': item, 'name': str(item)})
            elif isinstance(item, str):
                raw = item.strip()
                if raw.isdigit():
                    normalized.append({'id': int(raw), 'name': raw})
                elif ':' in raw:
                    category_id, name = raw.split(':', 1)
                    if category_id.strip().isdigit():
                        normalized.append({'id': int(category_id.strip()), 'name': name.strip() or category_id.strip()})
            elif isinstance(item, dict):
                category_id = item.get('id')
                if category_id is not None:
                    normalized.append({'id': int(category_id), 'name': str(item.get('name') or category_id)})
        return normalized


class WordPressSiteOut(WordPressSiteIn):
    id: int
    app_password_saved: bool = False
    app_password: str | None = None
    model_config = {'from_attributes': True}


class PublishJobRequest(BaseModel):
    pipeline_type: str = Field(default='recent', pattern='^(recent|retrospective)$')
    country_code: str
    country_name: str | None = None
    target_language: str | None = None
    hours_back: int = Field(default=1, ge=1, le=24)
    period_start: date | None = None
    period_end: date | None = None
    articles_per_day: int | None = Field(default=None, ge=1, le=100)
    site_limit: int | None = Field(default=None, ge=1)
    rewrite_model: str | None = None
    image_model: str | None = None
    stop_words: str | None = None
    specificity: str | None = None


class PublishJobOut(BaseModel):
    id: int
    status: str
    country_code: str
    country_name: str | None = None
    target_language: str
    hours_back: int
    pipeline_type: str = 'recent'
    period_start: datetime | None = None
    period_end: datetime | None = None
    articles_per_day: int | None = None
    planned_articles_per_site: int | None = None
    site_limit: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_after: datetime | None = None
    error_text: str | None = None
    model_config = {'from_attributes': True}


class PublishedArticleOut(BaseModel):
    id: int
    job_id: int | None = None
    site_id: int | None = None
    status: str
    title: str | None = None
    wp_post_id: int | None = None
    wp_media_id: int | None = None
    wp_link: str | None = None
    scheduled_for: datetime | None = None
    sequence_number: int | None = None
    source_article_ids: list[int] | None = None
    error_text: str | None = None
    created_at: datetime
    published_at: datetime | None = None
    model_config = {'from_attributes': True}
