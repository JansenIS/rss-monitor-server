from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Article, PublishedArticle, PublishingSettings, PublishJob, WordPressSite
from .utils import now_utc

DEFAULT_ROUTERAI_BASE_URL = 'https://routerai.ru/api/v1'
ROUTERAI_IMAGE_TIMEOUT_SECONDS = 900
ROUTERAI_IMAGE_RETRY_STATUSES = {502, 503, 504}
ROUTERAI_IMAGE_RETRY_DELAYS_SECONDS = (5, 15, 30)
DEFAULT_ROUTERAI_IMAGE_MODEL = 'openai/gpt-image-1'
STEREOTYPE_BAN = (
    'Do not include stereotypical nature or safari imagery: no rhinos, parrots, '
    'crocodiles, jungles, generic wildlife, or unrelated exotic landscapes. '
    'Avoid national or ethnic stereotypes, including clichéd traditional costumes, '
    'caricatured cultural props, or generic folk clothing unless they are directly relevant to the news event.'
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



def build_unique_article_snapshots(day_articles: list[dict[str, Any]], limit: int, used_article_ids: set[int] | None = None) -> list[list[dict[str, Any]]]:
    """Return single-article snapshots without reusing source articles."""
    used_article_ids = used_article_ids or set()
    snapshots: list[list[dict[str, Any]]] = []
    for article in day_articles:
        raw_id = article.get('id')
        if not isinstance(raw_id, int) or raw_id in used_article_ids:
            continue
        snapshots.append([article])
        used_article_ids.add(raw_id)
        if len(snapshots) >= limit:
            break
    return snapshots

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



def normalize_country_codes(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip().upper() for item in value if str(item).strip()]


def site_accepts_country(site: WordPressSite, country_code: str) -> bool:
    codes = normalize_country_codes(site.country_codes)
    return not codes or country_code.upper() in codes


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
        if site.generation_limit_reset_at and site.generation_limit_reset_at > since:
            since = site.generation_limit_reset_at
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
        f'and avoid flags or symbols of similar countries. {STEREOTYPE_BAN} '
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


class RouterAIImageError(RuntimeError):
    pass


def _routerai_image_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('data', 'images'):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


async def routerai_image(base_url: str, api_key: str, model: str, prompt: str, *, raise_on_error: bool = False) -> bytes | None:
    retry_delays = ROUTERAI_IMAGE_RETRY_DELAYS_SECONDS
    max_attempts = len(retry_delays) + 1
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=ROUTERAI_IMAGE_TIMEOUT_SECONDS) as client:
                resp = await client.post(f'{base_url.rstrip("/")}/images', headers=_auth_headers(api_key), json={'model': model, 'prompt': prompt, 'n': 1})
                if resp.status_code in ROUTERAI_IMAGE_RETRY_STATUSES and attempt < max_attempts - 1:
                    last_exc = httpx.HTTPStatusError(
                        f'Transient RouterAI image response: {resp.status_code}',
                        request=resp.request,
                        response=resp,
                    )
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                resp.raise_for_status()
                data = resp.json()
                for item in _routerai_image_items(data):
                    image_b64 = item.get('b64_json') or item.get('base64')
                    if image_b64:
                        return base64.b64decode(image_b64)
                    image_url = item.get('url') or item.get('image_url')
                    if image_url:
                        img = await client.get(image_url)
                        img.raise_for_status()
                        return img.content
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in ROUTERAI_IMAGE_RETRY_STATUSES and attempt < max_attempts - 1:
                await asyncio.sleep(retry_delays[attempt])
                continue
            if raise_on_error:
                raise RouterAIImageError(f'RouterAI image generation failed: {exc}') from exc
            return None
        break

    if raise_on_error:
        if last_exc is not None:
            raise RouterAIImageError(f'RouterAI image generation failed after {max_attempts} attempts: {last_exc}') from last_exc
        raise RouterAIImageError('RouterAI image generation returned no image data')
    return None


def _slug(text: str) -> str:
    slug = re.sub(r'[^\w\s-]', '', text.lower(), flags=re.UNICODE)
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:120] or 'article'


def _ascii_filename(text: str, extension: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    slug = _slug(ascii_name)
    return f'{slug}{extension}'


def _content_disposition_filename(text: str, extension: str) -> str:
    unicode_filename = f'{_slug(text)}{extension}'
    ascii_filename = _ascii_filename(text, extension)
    encoded_filename = quote(unicode_filename, safe='')
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"


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


class WordPressUploadError(RuntimeError):
    pass


def wordpress_api_base(site_base_url: str) -> str:
    base_url = site_base_url.rstrip('/')
    if base_url.endswith('/wp-json/wp/v2'):
        return base_url
    if base_url.endswith('/wp-json'):
        return f'{base_url}/wp/v2'
    return f'{base_url}/wp-json/wp/v2'


def _wordpress_error_message(exc: httpx.HTTPStatusError, action: str) -> str:
    status_code = exc.response.status_code
    url = str(exc.request.url)
    details = exc.response.text.strip()[:500]
    message = f'WordPress {action} failed with HTTP {status_code} for {url}'
    if status_code == 404 and '/wp-json/wp/v2' in url:
        message += '. WordPress REST API endpoint was not found; check that the configured base_url points to a WordPress site with REST API enabled.'
    if details:
        message += f' Response: {details}'
    return message


def _raise_wordpress_status(response: httpx.Response, action: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise WordPressUploadError(_wordpress_error_message(exc, action)) from exc


async def upload_to_wordpress(site: WordPressSite, article: dict[str, Any], image: bytes | None, image_prompt: str, publish_at: datetime | None = None) -> dict[str, Any]:
    auth = (site.username or '', site.app_password or '')
    base = wordpress_api_base(site.base_url)
    media_id = None
    async with httpx.AsyncClient(timeout=120, auth=auth) as client:
        api_resp = await client.get(base)
        _raise_wordpress_status(api_resp, 'REST API check')
        if image:
            media_resp = await client.post(f'{base}/media', headers={'Content-Disposition': _content_disposition_filename(article.get('title', 'image'), '.png'), 'Content-Type': 'image/png'}, content=image)
            _raise_wordpress_status(media_resp, 'media upload')
            media = media_resp.json()
            media_id = media.get('id')
            if media_id:
                alt_resp = await client.post(f'{base}/media/{media_id}', json={'alt_text': image_prompt[:250]})
                _raise_wordpress_status(alt_resp, 'media alt text update')
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
        _raise_wordpress_status(post_resp, 'post publishing')
        post = post_resp.json()
    return {'post_id': post.get('id'), 'link': post.get('link'), 'media_id': media_id, 'categories': post_payload['categories']}
