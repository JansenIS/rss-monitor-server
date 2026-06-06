from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated
import orjson
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, ORJSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal, init_db
from .importer import import_sources_payload
from .models import Article, FetchCommand, FetchRun, FetchSourceLog, Source
from .schemas import (
    ArticleOut,
    ArticlesPage,
    FetchRunOut,
    ImportResult,
    SourceLogOut,
    SourceOut,
    StartRunRequest,
    StartRunResponse,
    SyncArticlesOut,
)
from .utils import now_utc, parse_datetime_any

app = FastAPI(title='Local Media Monitor RSS Server', default_response_class=ORJSONResponse)


@app.on_event('startup')
def startup() -> None:
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    if not settings.api_token or settings.api_token == 'change_this_to_a_long_random_token':
        # Development convenience only. In production set API_TOKEN.
        return
    expected = f'Bearer {settings.api_token}'
    if authorization != expected:
        raise HTTPException(status_code=401, detail='Unauthorized')


@app.get('/', response_class=HTMLResponse)
def index(db: Session = Depends(get_db)) -> str:
    sources_total = db.scalar(select(func.count()).select_from(Source)) or 0
    sources_active = db.scalar(select(func.count()).select_from(Source).where(Source.is_active.is_(True))) or 0
    articles_total = db.scalar(select(func.count()).select_from(Article)) or 0
    last_run = db.execute(select(FetchRun).order_by(FetchRun.started_at.desc()).limit(1)).scalar_one_or_none()
    last_run_html = 'нет запусков'
    if last_run:
        last_run_html = f'#{last_run.id} — {last_run.status}, started={last_run.started_at}, finished={last_run.finished_at}, new={last_run.articles_new}'
    return f'''
    <html><head><meta charset="utf-8"><title>Local Media Monitor RSS Server</title></head>
    <body style="font-family: Segoe UI, Arial, sans-serif; max-width: 980px; margin: 40px auto; line-height: 1.5;">
      <h1>Local Media Monitor RSS Server</h1>
      <p>Статус: работает.</p>
      <ul>
        <li>Источников всего: {sources_total}</li>
        <li>Активных источников: {sources_active}</li>
        <li>Материалов: {articles_total}</li>
        <li>Последний проход: {last_run_html}</li>
      </ul>
      <p>API: <code>/api/v1/health</code>, <code>/api/v1/articles</code>, <code>/api/v1/sync/articles</code>, <code>/docs</code>.</p>
    </body></html>
    '''


@app.get('/api/v1/health')
def health(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    sources_total = db.scalar(select(func.count()).select_from(Source)) or 0
    sources_active = db.scalar(select(func.count()).select_from(Source).where(Source.is_active.is_(True))) or 0
    articles_total = db.scalar(select(func.count()).select_from(Article)) or 0
    last_run = db.execute(select(FetchRun).order_by(FetchRun.started_at.desc()).limit(1)).scalar_one_or_none()
    return {
        'status': 'ok',
        'server_time': now_utc(),
        'server_name': settings.server_name,
        'sources_total': sources_total,
        'sources_active': sources_active,
        'articles_total': articles_total,
        'fetch_interval_seconds': settings.fetch_interval_seconds,
        'fetch_concurrency': settings.fetch_concurrency,
        'last_run': FetchRunOut.model_validate(last_run).model_dump() if last_run else None,
    }


@app.post('/api/v1/sources/import', response_model=ImportResult)
async def import_sources(
    include_secondary: bool = Query(False),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    if not file:
        raise HTTPException(status_code=400, detail='Upload JSON file as multipart field "file"')
    raw = await file.read()
    try:
        payload = json.loads(raw.decode('utf-8-sig'))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Invalid JSON: {exc}')
    return import_sources_payload(db, payload, include_secondary=include_secondary)


@app.post('/api/v1/sources/import-json', response_model=ImportResult)
def import_sources_json(
    payload: dict,
    include_secondary: bool = Query(False),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    return import_sources_payload(db, payload, include_secondary=include_secondary)


@app.get('/api/v1/sources', response_model=list[SourceOut])
def list_sources(
    country_code: str | None = None,
    active: bool | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    stmt = select(Source).order_by(Source.id.asc()).limit(limit).offset(offset)
    if country_code:
        stmt = stmt.where(Source.country_code == country_code.upper())
    if active is not None:
        stmt = stmt.where(Source.is_active.is_(active))
    return list(db.execute(stmt).scalars().all())


@app.post('/api/v1/runs/start', response_model=StartRunResponse)
def start_run(
    request: StartRunRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    cmd = FetchCommand(command_type='fetch', country_code=request.country_code.upper() if request.country_code else None, status='queued')
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    return StartRunResponse(command_id=cmd.id, status=cmd.status)


@app.get('/api/v1/runs', response_model=list[FetchRunOut])
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    return list(db.execute(select(FetchRun).order_by(FetchRun.started_at.desc()).limit(limit)).scalars().all())


@app.get('/api/v1/runs/{run_id}/logs', response_model=list[SourceLogOut])
def run_logs(
    run_id: int,
    status: str | None = None,
    limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    stmt = select(FetchSourceLog).where(FetchSourceLog.run_id == run_id).order_by(FetchSourceLog.id.asc()).limit(limit)
    if status:
        stmt = stmt.where(FetchSourceLog.status == status)
    return list(db.execute(stmt).scalars().all())


def _article_query(
    country_code: str | None,
    source_type: str | None,
    language: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    date_field: str,
):
    stmt = select(Article)
    if country_code:
        stmt = stmt.where(Article.country_code == country_code.upper())
    if source_type:
        stmt = stmt.where(Article.source_type == source_type)
    if language:
        stmt = stmt.where(Article.language == language)
    column = Article.fetched_at if date_field == 'fetched_at' else Article.published_at
    if from_dt:
        stmt = stmt.where(column >= from_dt)
    if to_dt:
        stmt = stmt.where(column <= to_dt)
    return stmt


@app.get('/api/v1/articles', response_model=ArticlesPage)
def list_articles(
    country_code: str | None = None,
    source_type: str | None = None,
    language: str | None = None,
    from_: str | None = Query(default=None, alias='from'),
    to: str | None = None,
    date_field: str = Query('published_at', pattern='^(published_at|fetched_at)$'),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    from_dt = parse_datetime_any(from_) if from_ else None
    to_dt = parse_datetime_any(to) if to else None
    base = _article_query(country_code, source_type, language, from_dt, to_dt, date_field)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = db.scalar(count_stmt) or 0
    column = Article.fetched_at if date_field == 'fetched_at' else Article.published_at
    items = list(db.execute(base.order_by(column.desc().nullslast(), Article.id.desc()).limit(limit).offset(offset)).scalars().all())
    return ArticlesPage(total=total, limit=limit, offset=offset, items=items)


@app.get('/api/v1/sync/articles', response_model=SyncArticlesOut)
def sync_articles(
    after_id: int = Query(0, ge=0),
    country_code: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    stmt = select(Article).where(Article.id > after_id).order_by(Article.id.asc()).limit(limit + 1)
    if country_code:
        stmt = stmt.where(Article.country_code == country_code.upper())
    rows = list(db.execute(stmt).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after_id = rows[-1].id if rows else after_id
    return SyncArticlesOut(
        server_time=now_utc(),
        after_id=after_id,
        next_after_id=next_after_id,
        has_more=has_more,
        articles=rows,
    )


@app.get('/api/v1/export/articles.ndjson')
def export_articles_ndjson(
    country_code: str | None = None,
    from_: str | None = Query(default=None, alias='from'),
    to: str | None = None,
    date_field: str = Query('published_at', pattern='^(published_at|fetched_at)$'),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    from_dt = parse_datetime_any(from_) if from_ else None
    to_dt = parse_datetime_any(to) if to else None
    stmt = _article_query(country_code, None, None, from_dt, to_dt, date_field).order_by(Article.id.asc())

    def gen():
        for article in db.execute(stmt).scalars().yield_per(1000):
            data = ArticleOut.model_validate(article).model_dump(mode='json')
            yield orjson.dumps(data) + b'\n'

    return StreamingResponse(gen(), media_type='application/x-ndjson')
