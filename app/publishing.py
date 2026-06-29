from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Article, PublishedArticle, PublishingSettings, PublishJob, WordPressSite
from .utils import now_utc

DEFAULT_ROUTERAI_BASE_URL = 'https://routerai.ru/api/v1'
DEFAULT_ROUTERAI_IMAGE_MODEL = 'openai/gpt-image-1'
NATURE_STEREOTYPE_BAN = (
    'Do not include stereotypical nature or safari imagery: no rhinos, parrots, '
    'crocodiles, jungles, generic wildlife, or unrelated exotic landscapes.'
)


def get_or_create_settings(db: Session) -> PublishingSettings:
    settings = db.get(PublishingSettings, 1)
    if settings:
        return settings
    settings = PublishingSettings(id=1)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def select_recent_articles(db: Session, country_code: str, hours_back: int = 1) -> list[Article]:
    since = now_utc() - timedelta(hours=max(1, hours_back))
    stmt = (
        select(Article)
        .where(Article.country_code == country_code.upper())
        .where((Article.published_at >= since) | (Article.fetched_at >= since))
        .where(Article.publishing_used_at.is_(None))
        .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
        .limit(50)
    )
    return list(db.execute(stmt).scalars().all())


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(day, time.max, tzinfo=timezone.utc)
    return start, end


def iter_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError('period_end must be greater than or equal to period_start')
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def select_articles_for_day(db: Session, country_code: str, day: date, limit: int = 50) -> list[Article]:
    start, end = _day_bounds(day)
    stmt = (
        select(Article)
        .where(Article.country_code == country_code.upper())
        .where((Article.published_at >= start) & (Article.published_at <= end))
        .where(Article.publishing_used_at.is_(None))
        .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
        .limit(limit)
    )
    articles = list(db.execute(stmt).scalars().all())
    if articles:
        return articles
    stmt = (
        select(Article)
        .where(Article.country_code == country_code.upper())
        .where((Article.fetched_at >= start) & (Article.fetched_at <= end))
        .where(Article.publishing_used_at.is_(None))
        .order_by(Article.fetched_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def build_retrospective_snapshot(db: Session, country_code: str, start: date, end: date, articles_per_day: int) -> dict[str, Any]:
    days = []
    for day in iter_days(start, end):
        articles = select_articles_for_day(db, country_code, day, limit=max(50, articles_per_day * 5))
        days.append({'date': day.isoformat(), 'articles': build_articles_snapshot(articles)})
    return {
        'mode': 'retrospective',
        'period_start': start.isoformat(),
        'period_end': end.isoformat(),
        'articles_per_day': articles_per_day,
        'days': days,
    }


def build_articles_snapshot(articles: list[Article]) -> list[dict[str, Any]]:
    return [
        {
            'id': article.id,
            'title': article.title,
            'summary': article.summary,
            'content': article.content,
            'url': article.url,
            'source_name': article.source_name,
            'published_at': article.published_at.isoformat() if article.published_at else None,
        }
        for article in articles
    ]


def source_ids_from_snapshot(snapshot: list[dict[str, Any]]) -> list[int]:
    ids = []
    for item in snapshot:
        raw_id = item.get('id')
        if isinstance(raw_id, int) and raw_id not in ids:
            ids.append(raw_id)
    return ids


def mark_source_articles_used(db: Session, article_ids: list[int], job_id: int) -> None:
    if not article_ids:
        return
    articles = db.execute(select(Article).where(Article.id.in_(article_ids))).scalars().all()
    used_at = now_utc()
    for article in articles:
        if article.publishing_used_at is None:
            article.publishing_used_at = used_at
            article.publishing_job_id = job_id


def site_generation_delay(db: Session, site: WordPressSite) -> datetime | None:
    now = now_utc()
    limits = [
        (site.generation_limit_per_hour, timedelta(hours=1)),
        (site.generation_limit_per_24h, timedelta(hours=24)),
    ]
    retry_after: datetime | None = None
    for limit, window in limits:
        if not limit:
            continue
        since = now - window
        rows = list(db.execute(
            select(PublishedArticle.created_at)
            .where(PublishedArticle.site_id == site.id)
            .where(PublishedArticle.status.in_(['running', 'published', 'failed']))
            .where(PublishedArticle.created_at >= since)
            .order_by(PublishedArticle.created_at.asc())
        ).scalars().all())
        if len(rows) >= limit:
            candidate = rows[0] + window
            retry_after = candidate if retry_after is None else min(retry_after, candidate)
    return retry_after


def published_slot_exists(db: Session, job_id: int, site_id: int, scheduled_for: datetime | None, sequence_number: int | None) -> bool:
    stmt = select(PublishedArticle.id).where(
        PublishedArticle.job_id == job_id,
        PublishedArticle.site_id == site_id,
        PublishedArticle.status.in_(['running', 'published']),
    )
    if scheduled_for is None:
        stmt = stmt.where(PublishedArticle.scheduled_for.is_(None))
    else:
        stmt = stmt.where(PublishedArticle.scheduled_for == scheduled_for)
    if sequence_number is None:
        stmt = stmt.where(PublishedArticle.sequence_number.is_(None))
    else:
        stmt = stmt.where(PublishedArticle.sequence_number == sequence_number)
    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def build_image_prompt(article_title: str, country_name: str, country_code: str) -> str:
    return (
        f'Create an editorial news illustration for an article about {article_title!r}. '
        f'The specific country is {country_name} ({country_code.upper()}); make visual cues accurate to this exact country '
        f'and avoid flags or symbols of similar countries. {NATURE_STEREOTYPE_BAN} '
        'Use a modern press-photo style, realistic lighting, no text overlays, no logos.'
    )


def _auth_headers(api_key: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}


async def routerai_chat(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f'{base_url.rstrip("/")}/chat/completions', headers=_auth_headers(api_key), json={'model': model, 'messages': messages, 'response_format': {'type': 'json_object'}})
        resp.raise_for_status()
        data = resp.json()
    content = data['choices'][0]['message']['content']
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {'title': 'Generated article', 'content': content, 'excerpt': content[:240], 'categories': []}


async def routerai_image(base_url: str, api_key: str, model: str, prompt: str) -> bytes | None:
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f'{base_url.rstrip("/")}/images', headers=_auth_headers(api_key), json={'model': model, 'prompt': prompt, 'n': 1})
        resp.raise_for_status()
        data = resp.json()
        item = data.get('data', [{}])[0]
        if item.get('b64_json'):
            return base64.b64decode(item['b64_json'])
        if item.get('url'):
            img = await client.get(item['url'])
            img.raise_for_status()
            return img.content
    return None


def _slug(text: str) -> str:
    slug = re.sub(r'[^\w\s-]', '', text.lower(), flags=re.UNICODE)
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:120] or 'article'


def _coerce_category_id(value: Any) -> int | None:
    try:
        category_id = int(value)
    except (TypeError, ValueError):
        return None
    return category_id if category_id > 0 else None


def category_ids(categories: list | None) -> list[int]:
    ids: list[int] = []
    for category in categories or []:
        category_id = None
        if isinstance(category, (int, str)):
            category_id = _coerce_category_id(category)
        elif isinstance(category, dict):
            category_id = _coerce_category_id(category.get('id'))
        if category_id is not None and category_id not in ids:
            ids.append(category_id)
    return ids


def category_options(categories: list | None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for category in categories or []:
        if isinstance(category, (int, str)):
            category_id = _coerce_category_id(category)
            if category_id is not None:
                options.append({'id': category_id, 'name': str(category_id)})
        elif isinstance(category, dict):
            category_id = _coerce_category_id(category.get('id'))
            if category_id is not None:
                options.append({'id': category_id, 'name': str(category.get('name') or category_id)})
    return options


def wordpress_post_category_ids(article_categories: Any, site_categories: list | None) -> list[int]:
    fallback_ids = category_ids(site_categories)
    selected_ids = category_ids(article_categories)
    if not selected_ids:
        return fallback_ids
    if not fallback_ids:
        return selected_ids
    allowed = set(fallback_ids)
    filtered_ids = [category_id for category_id in selected_ids if category_id in allowed]
    return filtered_ids or fallback_ids


def build_rewrite_messages(snapshot: list[dict[str, Any]], site: WordPressSite, job: PublishJob, planned_date: str | None = None, sequence_number: int | None = None) -> list[dict[str, str]]:
    country = job.country_name or job.country_code
    return [
        {'role': 'system', 'content': 'You are an SEO editor. Return only valid JSON with keys: title, slug, excerpt, content_html, seo_title, meta_description, category_ids. Choose category_ids only from the provided WordPress categories.'},
        {'role': 'user', 'content': json.dumps({
            'task': 'Write a unique SEO-optimized article from the news digest. Do not copy source wording. Adapt angle to this site only. If planned_publication_date is provided, write as if published on that historical date without saying this is retrospective.',
            'country': country,
            'target_language': site.language or job.target_language,
            'site_name': site.name,
            'site_specificity': site.specificity or job.specificity,
            'available_wordpress_categories': category_options(site.categories),
            'category_instruction': 'Categorize the article while writing it and return category_ids selected only from available_wordpress_categories.id. Use category names to choose the best semantic match.',
            'stop_words': job.stop_words,
            'source_news': snapshot,
            'planned_publication_date': planned_date,
            'daily_article_number': sequence_number,
        }, ensure_ascii=False)},
    ]


async def upload_to_wordpress(site: WordPressSite, article: dict[str, Any], image: bytes | None, image_prompt: str, publish_at: datetime | None = None) -> dict[str, Any]:
    auth = (site.username or '', site.app_password or '')
    base = site.base_url.rstrip('/') + '/wp-json/wp/v2'
    media_id = None
    async with httpx.AsyncClient(timeout=120, auth=auth) as client:
        if image:
            media_resp = await client.post(f'{base}/media', headers={'Content-Disposition': f'attachment; filename="{_slug(article.get("title", "image"))}.png"', 'Content-Type': 'image/png'}, content=image)
            media_resp.raise_for_status()
            media = media_resp.json()
            media_id = media.get('id')
            if media_id:
                alt_resp = await client.post(f'{base}/media/{media_id}', json={'alt_text': image_prompt[:250]})
                alt_resp.raise_for_status()
        post_payload = {
            'title': article.get('title'),
            'slug': article.get('slug') or _slug(article.get('title', 'article')),
            'excerpt': article.get('excerpt'),
            'content': article.get('content_html') or article.get('content'),
            'status': site.default_status or 'draft',
            'categories': wordpress_post_category_ids(article.get('category_ids'), site.categories),
        }
        if publish_at:
            post_payload['date'] = publish_at.isoformat()
        if media_id:
            post_payload['featured_media'] = media_id
        post_resp = await client.post(f'{base}/posts', json=post_payload)
        post_resp.raise_for_status()
        post = post_resp.json()
    return {'post_id': post.get('id'), 'link': post.get('link'), 'media_id': media_id, 'categories': post_payload['categories']}
