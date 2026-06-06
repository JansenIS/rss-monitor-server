from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from dateutil import parser as date_parser


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8', errors='ignore')).hexdigest()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    normalized = url.strip()
    if not normalized:
        return None
    normalized = normalized.replace('&amp;', '&')
    normalized = re.sub(r'\s+', '', normalized)
    return normalized


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', ' ', value, flags=re.I)
    text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"')
    text = re.sub(r'\s+', ' ', text).strip()
    return text or None


def parse_datetime_any(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    raw = str(value).strip()
    if not raw:
        return None

    # RFC822 / email-style RSS dates.
    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO, common EU/US formats, French/English month names handled by dateutil partly.
    try:
        dt = date_parser.parse(raw, dayfirst=True, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def extract_date_from_url(url: str | None) -> datetime | None:
    if not url:
        return None
    patterns = [
        r'(?P<y>20\d{2})[/-](?P<m>\d{1,2})[/-](?P<d>\d{1,2})',
        r'(?P<d>\d{1,2})[.-](?P<m>\d{1,2})[.-](?P<y>20\d{2})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if not m:
            continue
        try:
            return datetime(
                int(m.group('y')),
                int(m.group('m')),
                int(m.group('d')),
                tzinfo=timezone.utc,
            )
        except Exception:
            continue
    return None
