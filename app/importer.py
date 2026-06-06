from __future__ import annotations
from typing import Any
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .models import Source
from .schemas import ImportResult
from .utils import normalize_url, now_utc


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


def _upsert_source(db: Session, row: dict[str, Any], result: ImportResult) -> None:
    rss_url = normalize_url(row.get('rss_url'))
    homepage_url = normalize_url(row.get('homepage_url'))
    source_name = (row.get('source_name') or row.get('name') or '').strip()
    country_code = (row.get('country_code') or row.get('iso2') or row.get('iso3') or '').strip() or None
    if country_code:
        country_code = country_code.upper()

    if not source_name or not (rss_url or homepage_url):
        result.sources_skipped += 1
        return

    q = select(Source).where(
        or_(
            Source.rss_url == rss_url if rss_url else False,
            Source.homepage_url == homepage_url if homepage_url else False,
        )
    ).limit(1)
    existing = db.execute(q).scalar_one_or_none()
    now = now_utc()

    if existing:
        existing.country_code = country_code or existing.country_code
        existing.country_name = row.get('country_name') or existing.country_name
        existing.source_name = source_name or existing.source_name
        existing.source_type = row.get('source_type') or existing.source_type
        existing.homepage_url = homepage_url or existing.homepage_url
        existing.rss_url = rss_url or existing.rss_url
        existing.sitemap_url = normalize_url(row.get('sitemap_url')) or existing.sitemap_url
        existing.language = row.get('language') or existing.language
        existing.priority = int(row.get('priority') or existing.priority or 3)
        existing.reliability = int(row.get('reliability') or existing.reliability or 3)
        existing.is_active = bool(row.get('is_active', existing.is_active))
        existing.updated_at = now
        result.sources_updated += 1
        return

    db.add(Source(
        country_code=country_code,
        country_name=row.get('country_name'),
        source_name=source_name,
        source_type=row.get('source_type') or 'news_site',
        homepage_url=homepage_url,
        rss_url=rss_url,
        sitemap_url=normalize_url(row.get('sitemap_url')),
        language=row.get('language'),
        priority=int(row.get('priority') or 3),
        reliability=int(row.get('reliability') or 3),
        is_active=bool(row.get('is_active', True)),
    ))
    result.sources_added += 1


def import_sources_payload(db: Session, payload: dict[str, Any], include_secondary: bool = False) -> ImportResult:
    result = ImportResult()

    countries = payload.get('countries')
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
    elif isinstance(payload.get('sources'), list):
        for row in payload['sources']:
            if isinstance(row, dict):
                _upsert_source(db, row, result)
    elif isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                _upsert_source(db, row, result)
    else:
        result.errors.append('Unsupported JSON format: expected countries object, sources array, or array of sources')

    db.commit()
    return result
