from __future__ import annotations

from typing import Any
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .models import Article, Source
from .schemas import ImportResult
from .utils import normalize_url, now_utc, parse_datetime_any, sha256_text


def _first_rss_url(rss_items: Any) -> tuple[str | None, str | None]:
    if not isinstance(rss_items, list):
        return None, None
    for item in rss_items:
        if isinstance(item, dict):
            url = normalize_url(item.get('url'))
            language = item.get('language')
            if url:
                return url, language
    return None, None


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {'1', 'true', 'yes', 'y', 'да', 'on'}:
        return True
    if raw in {'0', 'false', 'no', 'n', 'нет', 'off'}:
        return False
    return default


def _source_lookup_query(rss_url: str | None, homepage_url: str | None, source_name: str | None, country_code: str | None):
    checks = []
    if rss_url:
        checks.append(Source.rss_url == rss_url)
    if homepage_url:
        checks.append(Source.homepage_url == homepage_url)
    if source_name:
        if country_code:
            checks.append((Source.source_name == source_name) & (Source.country_code == country_code))
        else:
            checks.append(Source.source_name == source_name)
    if not checks:
        return None
    return select(Source).where(or_(*checks)).limit(1)


def upsert_source_row(db: Session, row: dict[str, Any], result: ImportResult | None = None) -> tuple[int | None, bool]:
    """Insert or update a source. Returns (server_source_id, inserted)."""
    rss_url = normalize_url(row.get('rss_url'))
    homepage_url = normalize_url(row.get('homepage_url') or row.get('site_url'))
    source_name = (row.get('source_name') or row.get('name') or '').strip()
    country_code = (row.get('country_code') or row.get('iso2') or row.get('iso3') or '').strip() or None
    if country_code:
        country_code = country_code.upper()

    if not source_name or not (rss_url or homepage_url):
        if result:
            result.sources_skipped += 1
        return None, False

    q = _source_lookup_query(rss_url, homepage_url, source_name, country_code)
    existing = db.execute(q).scalar_one_or_none() if q is not None else None
    now = now_utc()

    if existing:
        existing.country_code = country_code or existing.country_code
        existing.country_name = row.get('country_name') or existing.country_name
        existing.source_name = source_name or existing.source_name
        existing.source_type = row.get('source_type') or row.get('type') or existing.source_type
        existing.homepage_url = homepage_url or existing.homepage_url
        existing.rss_url = rss_url or existing.rss_url
        existing.sitemap_url = normalize_url(row.get('sitemap_url')) or existing.sitemap_url
        existing.language = row.get('language') or existing.language
        existing.priority = _safe_int(row.get('priority'), existing.priority or 3)
        existing.reliability = _safe_int(row.get('reliability'), existing.reliability or 3)
        existing.is_active = _safe_bool(row.get('is_active'), existing.is_active)
        existing.updated_at = now
        if result:
            result.sources_updated += 1
        return existing.id, False

    obj = Source(
        country_code=country_code,
        country_name=row.get('country_name'),
        source_name=source_name,
        source_type=row.get('source_type') or row.get('type') or 'news_site',
        homepage_url=homepage_url,
        rss_url=rss_url,
        sitemap_url=normalize_url(row.get('sitemap_url')),
        language=row.get('language'),
        priority=_safe_int(row.get('priority'), 3),
        reliability=_safe_int(row.get('reliability'), 3),
        is_active=_safe_bool(row.get('is_active'), True),
    )
    db.add(obj)
    db.flush()
    if result:
        result.sources_added += 1
    return obj.id, True


def _upsert_source(db: Session, row: dict[str, Any], result: ImportResult) -> None:
    upsert_source_row(db, row, result)


def _source_to_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        'country_code': source.get('country_code'),
        'country_name': source.get('country_name'),
        'source_name': source.get('source_name') or source.get('name'),
        'source_type': source.get('source_type') or source.get('type') or 'news_site',
        'homepage_url': source.get('homepage_url') or source.get('site_url'),
        'rss_url': source.get('rss_url'),
        'sitemap_url': source.get('sitemap_url'),
        'language': source.get('language'),
        'priority': source.get('priority'),
        'reliability': source.get('reliability'),
        'is_active': source.get('is_active', True),
    }


def _resolve_source_for_mention(db: Session, mention: dict[str, Any], source_map: dict[int, int]) -> Source | None:
    old_id = mention.get('source_id')
    try:
        old_id_int = int(old_id) if old_id is not None else None
    except Exception:
        old_id_int = None
    if old_id_int is not None and old_id_int in source_map:
        return db.get(Source, source_map[old_id_int])

    source_name = (mention.get('source_name') or '').strip()
    country_code = (mention.get('country_code') or '').strip().upper() or None
    if not source_name:
        return None

    stmt = select(Source).where(Source.source_name == source_name).limit(1)
    if country_code:
        stmt = select(Source).where(Source.source_name == source_name, Source.country_code == country_code).limit(1)
    return db.execute(stmt).scalar_one_or_none()


def _insert_article_from_mention(db: Session, mention: dict[str, Any], source: Source | None, result: ImportResult) -> None:
    url = normalize_url(mention.get('url'))
    title = (mention.get('title') or '').strip()
    if not url or not title:
        result.articles_skipped += 1
        return

    url_hash = sha256_text(url)
    existing = db.execute(select(Article).where(Article.url_hash == url_hash).limit(1)).scalar_one_or_none()
    if existing:
        # Fill missing fields only; do not clobber server-collected richer data.
        if not existing.summary and mention.get('summary'):
            existing.summary = mention.get('summary')
        if not existing.content and mention.get('content'):
            existing.content = mention.get('content')
        if not existing.published_at and mention.get('published_at'):
            existing.published_at = parse_datetime_any(mention.get('published_at'))
        if not existing.author and mention.get('author'):
            existing.author = mention.get('author')
        result.articles_duplicate += 1
        return

    published_at = parse_datetime_any(mention.get('published_at'))
    fetched_at = parse_datetime_any(mention.get('created_at')) or now_utc()
    content_hash = mention.get('content_hash') or sha256_text(f"{title}\n{url}\n{mention.get('content') or mention.get('summary') or ''}")

    raw_json = {
        'collector': 'tauri_full_export_import',
        'original_mention_id': mention.get('id'),
        'source_name': mention.get('source_name'),
        'sentiment': mention.get('sentiment'),
        'imported_at': now_utc().isoformat(),
    }

    db.add(Article(
        source_id=source.id if source else None,
        country_code=(mention.get('country_code') or (source.country_code if source else None)),
        country_name=(source.country_name if source else None),
        source_name=(mention.get('source_name') or (source.source_name if source else None)),
        source_type=(source.source_type if source else None),
        url=url,
        canonical_url=normalize_url(mention.get('canonical_url')),
        title=title,
        summary=mention.get('summary'),
        content=mention.get('content'),
        author=mention.get('author'),
        language=mention.get('language'),
        published_at=published_at,
        fetched_at=fetched_at,
        content_hash=content_hash,
        url_hash=url_hash,
        raw_json=raw_json,
    ))
    result.articles_added += 1


def import_tauri_transfer_payload(
    db: Session,
    payload: dict[str, Any],
    import_sources: bool = True,
    import_mentions: bool = True,
) -> ImportResult:
    result = ImportResult()
    schema = str(payload.get('schema') or '').strip()
    if schema != 'local-media-monitor-transfer-v1':
        result.errors.append(f'Unsupported Tauri transfer schema: {schema or "<empty>"}')
        return result

    source_map: dict[int, int] = {}
    if import_sources:
        for source in payload.get('sources') or []:
            if not isinstance(source, dict):
                result.sources_skipped += 1
                continue
            server_id, _inserted = upsert_source_row(db, _source_to_row(source), result)
            if server_id is not None:
                try:
                    source_map[int(source.get('id'))] = server_id
                except Exception:
                    pass

    if import_mentions:
        for mention in payload.get('mentions') or []:
            if not isinstance(mention, dict):
                result.articles_skipped += 1
                continue
            source = _resolve_source_for_mention(db, mention, source_map)
            _insert_article_from_mention(db, mention, source, result)

    db.commit()
    return result


def import_sources_payload(db: Session, payload: dict[str, Any], include_secondary: bool = False) -> ImportResult:
    # Full database export from the Tauri client: "Transfer → Выгрузить всё".
    if isinstance(payload, dict) and payload.get('schema') == 'local-media-monitor-transfer-v1':
        return import_tauri_transfer_payload(db, payload, import_sources=True, import_mentions=True)

    result = ImportResult()

    countries = payload.get('countries') if isinstance(payload, dict) else None
    if isinstance(countries, dict):
        for iso3, country in countries.items():
            if not isinstance(country, dict):
                continue
            country_name = country.get('country_en') or country.get('country_ru') or iso3
            country_code = country.get('iso2') or country.get('iso3') or iso3

            for src in country.get('local_sources') or []:
                if not isinstance(src, dict):
                    continue
                rss_url, rss_lang = _first_rss_url(src.get('rss'))
                _upsert_source(db, {
                    'country_code': country_code,
                    'country_name': country_name,
                    'source_name': src.get('name'),
                    'source_type': src.get('type') or 'news_site',
                    'homepage_url': src.get('site_url'),
                    'rss_url': rss_url,
                    'language': rss_lang,
                    'priority': 3,
                    'reliability': 3,
                }, result)

            if include_secondary:
                for src in country.get('secondary_aggregators') or []:
                    if not isinstance(src, dict):
                        continue
                    rss_url, rss_lang = _first_rss_url(src.get('rss'))
                    _upsert_source(db, {
                        'country_code': country_code,
                        'country_name': country_name,
                        'source_name': src.get('name'),
                        'source_type': src.get('type') or 'aggregator',
                        'homepage_url': src.get('site_url'),
                        'rss_url': rss_url,
                        'language': rss_lang,
                        'priority': 1,
                        'reliability': 2,
                    }, result)
    elif isinstance(payload, dict) and isinstance(payload.get('sources'), list):
        for row in payload['sources']:
            if isinstance(row, dict):
                _upsert_source(db, row, result)
    elif isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                _upsert_source(db, row, result)
    else:
        result.errors.append('Unsupported JSON format: expected countries object, Tauri transfer bundle, sources array, or array of sources')

    db.commit()
    return result
