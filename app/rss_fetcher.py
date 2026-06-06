from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any
import feedparser
import httpx
from .config import settings
from .models import Source
from .utils import normalize_url, sha256_text, strip_html, parse_datetime_any, extract_date_from_url


@dataclass
class ParsedArticle:
    url: str
    canonical_url: str | None
    title: str | None
    summary: str | None
    content: str | None
    author: str | None
    language: str | None
    published_at: Any
    content_hash: str | None
    url_hash: str
    raw_json: dict[str, Any]


@dataclass
class SourceFetchResult:
    source_id: int
    source_name: str
    rss_url: str | None
    ok: bool
    http_status: int | None = None
    error: str | None = None
    articles: list[ParsedArticle] = field(default_factory=list)


def _entry_link(entry: Any) -> str | None:
    link = normalize_url(entry.get('link'))
    if link:
        return link
    links = entry.get('links') or []
    if isinstance(links, list):
        for item in links:
            if isinstance(item, dict):
                candidate = normalize_url(item.get('href'))
                if candidate:
                    return candidate
    return None


def _entry_date(entry: Any) -> Any:
    for key in ('published_parsed', 'updated_parsed', 'created_parsed', 'expired_parsed'):
        value = entry.get(key)
        if value:
            return value
    for key in ('published', 'updated', 'created', 'date', 'dc_date'):
        value = entry.get(key)
        if value:
            return value
    return None


def _entry_content(entry: Any) -> str | None:
    content_items = entry.get('content') or []
    if isinstance(content_items, list):
        parts = []
        for item in content_items:
            if isinstance(item, dict) and item.get('value'):
                parts.append(str(item.get('value')))
        if parts:
            return strip_html('\n'.join(parts))
    return strip_html(entry.get('summary') or entry.get('description'))


def parse_feed_bytes(data: bytes, source: Source, limit: int) -> list[ParsedArticle]:
    feed = feedparser.parse(data)
    entries = feed.entries or []
    articles: list[ParsedArticle] = []

    for entry in entries[:limit]:
        url = _entry_link(entry)
        if not url:
            title_for_hash = str(entry.get('title') or '').strip()
            if not title_for_hash:
                continue
            url = f'urn:local:{source.id}:{sha256_text(title_for_hash)}'

        title = strip_html(entry.get('title'))
        content = _entry_content(entry)
        summary = strip_html(entry.get('summary') or entry.get('description'))
        author = strip_html(entry.get('author'))
        published = parse_datetime_any(_entry_date(entry)) or extract_date_from_url(url)
        content_hash = sha256_text((title or '') + '\n' + (content or summary or '')) if (title or content or summary) else None
        url_hash = sha256_text(url)

        raw = {
            'title': entry.get('title'),
            'link': entry.get('link'),
            'published': entry.get('published'),
            'updated': entry.get('updated'),
            'author': entry.get('author'),
            'summary': entry.get('summary'),
        }

        articles.append(ParsedArticle(
            url=url,
            canonical_url=url,
            title=title,
            summary=summary,
            content=content,
            author=author,
            language=source.language,
            published_at=published,
            content_hash=content_hash,
            url_hash=url_hash,
            raw_json=raw,
        ))
    return articles


async def fetch_source(client: httpx.AsyncClient, source: Source) -> SourceFetchResult:
    if not source.rss_url:
        return SourceFetchResult(source.id, source.source_name, source.rss_url, False, error='Source has no rss_url')

    try:
        response = await client.get(source.rss_url)
        status = response.status_code
        response.raise_for_status()
        articles = parse_feed_bytes(response.content, source, settings.max_articles_per_source)
        if not articles:
            return SourceFetchResult(source.id, source.source_name, source.rss_url, False, http_status=status, error='Feed parsed but no entries found')
        return SourceFetchResult(source.id, source.source_name, source.rss_url, True, http_status=status, articles=articles)
    except httpx.HTTPStatusError as exc:
        return SourceFetchResult(source.id, source.source_name, source.rss_url, False, http_status=exc.response.status_code, error=f'HTTP {exc.response.status_code}: {exc}')
    except Exception as exc:
        return SourceFetchResult(source.id, source.source_name, source.rss_url, False, error=repr(exc))


async def fetch_sources_parallel(sources: list[Source], concurrency: int) -> list[SourceFetchResult]:
    timeout = httpx.Timeout(settings.fetch_timeout_seconds, connect=min(settings.fetch_timeout_seconds, 10))
    headers = {
        'User-Agent': settings.fetch_user_agent,
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
        'Accept-Language': 'ru,en,fr;q=0.9,*;q=0.5',
    }
    limits = httpx.Limits(max_connections=max(concurrency * 2, 10), max_keepalive_connections=max(concurrency, 10))
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, limits=limits) as client:
        async def run_one(src: Source) -> SourceFetchResult:
            async with semaphore:
                return await fetch_source(client, src)

        return await asyncio.gather(*(run_one(source) for source in sources))
