from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated
import orjson
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, ORJSONResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal, init_db
from .importer import import_sources_payload, import_tauri_transfer_payload
from .models import Article, FetchCommand, FetchRun, FetchSourceLog, PublishedArticle, PublishingSettings, PublishJob, Source, WordPressSite
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
    PublishedArticleOut,
    PublishingSettingsIn,
    PublishingSettingsOut,
    PublishJobOut,
    PublishJobRequest,
    WordPressSiteIn,
    WordPressSiteOut,
)
from .utils import now_utc, parse_datetime_any
from .publishing import build_articles_snapshot, build_retrospective_snapshot, get_or_create_settings, iter_days, select_recent_articles

app = FastAPI(title='Local Media Monitor RSS Server', default_response_class=ORJSONResponse)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_methods=['*'],
    allow_headers=['*'],
)


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
      <p>Publishing admin: <a href="/admin">/admin</a>.</p>
      <p>API: <code>/api/v1/health</code>, <code>/api/v1/articles</code>, <code>/api/v1/sync/articles</code>, <code>/docs</code>.</p>
    </body></html>
    '''


@app.get('/admin', response_class=HTMLResponse)
def publishing_admin() -> HTMLResponse:
    admin_path = Path(__file__).resolve().parent.parent / 'admin' / 'publishing-admin.html'
    return HTMLResponse(admin_path.read_text(encoding='utf-8'))


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


@app.post('/api/v1/database/import', response_model=ImportResult)
async def import_tauri_database_file(
    import_sources: bool = Query(True),
    import_mentions: bool = Query(True),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Import full JSON export created by Tauri: Transfer -> Export all."""
    if not file:
        raise HTTPException(status_code=400, detail='Upload Tauri full database JSON as multipart field "file"')
    raw = await file.read()
    try:
        payload = json.loads(raw.decode('utf-8-sig'))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Invalid JSON: {exc}')
    return import_tauri_transfer_payload(
        db,
        payload,
        import_sources=import_sources,
        import_mentions=import_mentions,
    )


@app.post('/api/v1/database/import-json', response_model=ImportResult)
def import_tauri_database_json(
    payload: dict,
    import_sources: bool = Query(True),
    import_mentions: bool = Query(True),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Import full JSON export created by Tauri: Transfer -> Export all."""
    return import_tauri_transfer_payload(
        db,
        payload,
        import_sources=import_sources,
        import_mentions=import_mentions,
    )


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


def _settings_out(settings_obj: PublishingSettings) -> PublishingSettingsOut:
    data = PublishingSettingsOut.model_validate(settings_obj).model_dump()
    saved = bool(settings_obj.routerai_api_key)
    data['routerai_api_key_saved'] = saved
    data['routerai_api_key'] = '********' if saved else None
    return PublishingSettingsOut(**data)


@app.get('/api/v1/publishing/settings', response_model=PublishingSettingsOut)
def get_publishing_settings(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    return _settings_out(get_or_create_settings(db))


@app.put('/api/v1/publishing/settings', response_model=PublishingSettingsOut)
@app.post('/api/v1/publishing/settings', response_model=PublishingSettingsOut)
def update_publishing_settings(payload: PublishingSettingsIn, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    settings_obj = get_or_create_settings(db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == 'routerai_api_key' and (value is None or value == '' or value == '********'):
            continue
        setattr(settings_obj, field, value)
    if not settings_obj.routerai_base_url:
        settings_obj.routerai_base_url = 'https://routerai.ru/api/v1'
    db.commit()
    db.refresh(settings_obj)
    return _settings_out(settings_obj)


def _site_out(site: WordPressSite) -> WordPressSiteOut:
    data = WordPressSiteOut.model_validate(site).model_dump()
    saved = bool(site.app_password)
    data['app_password_saved'] = saved
    data['app_password'] = '********' if saved else None
    return WordPressSiteOut(**data)



@app.get('/api/v1/publishing/countries')
def list_publishing_countries(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    rows = db.execute(
        select(Source.country_code, func.max(Source.country_name))
        .where(Source.country_code.is_not(None))
        .group_by(Source.country_code)
        .order_by(Source.country_code.asc())
    ).all()
    return [
        {'code': code, 'name': name}
        for code, name in rows
        if code
    ]

@app.get('/api/v1/publishing/sites', response_model=list[WordPressSiteOut])
def list_wordpress_sites(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    return [_site_out(site) for site in db.execute(select(WordPressSite).order_by(WordPressSite.id.asc())).scalars().all()]


@app.post('/api/v1/publishing/sites', response_model=WordPressSiteOut)
def create_wordpress_site(payload: WordPressSiteIn, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    site = WordPressSite(**payload.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    return _site_out(site)


@app.put('/api/v1/publishing/sites/{site_id}', response_model=WordPressSiteOut)
def update_wordpress_site(site_id: int, payload: WordPressSiteIn, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    site = db.get(WordPressSite, site_id)
    if not site:
        raise HTTPException(status_code=404, detail='Site not found')
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == 'app_password' and (value is None or value == '' or value == '********'):
            continue
        setattr(site, field, value)
    db.commit()
    db.refresh(site)
    return _site_out(site)


@app.post('/api/v1/publishing/sites/{site_id}/limits/reset', response_model=WordPressSiteOut)
def reset_wordpress_site_limits(site_id: int, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    site = db.get(WordPressSite, site_id)
    if not site:
        raise HTTPException(status_code=404, detail='Site not found')
    site.generation_limit_reset_at = now_utc()
    db.commit()
    db.refresh(site)
    return _site_out(site)


@app.post('/api/v1/publishing/limits/reset')
def reset_all_wordpress_site_limits(db: Session = Depends(get_db), _: None = Depends(require_auth)):
    reset_at = now_utc()
    result = db.execute(update(WordPressSite).values(generation_limit_reset_at=reset_at))
    db.commit()
    return {'status': 'ok', 'reset_at': reset_at, 'sites_updated': result.rowcount or 0}


@app.delete('/api/v1/publishing/sites/{site_id}')
def delete_wordpress_site(site_id: int, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    site = db.get(WordPressSite, site_id)
    if not site:
        raise HTTPException(status_code=404, detail='Site not found')
    db.delete(site)
    db.commit()
    return {'status': 'deleted'}


@app.get('/api/v1/publishing/recent-news')
def recent_news_for_country(country_code: str, hours_back: int = Query(1, ge=1, le=24), db: Session = Depends(get_db), _: None = Depends(require_auth)):
    articles = select_recent_articles(db, country_code, hours_back)
    return {'country_code': country_code.upper(), 'hours_back': hours_back, 'total': len(articles), 'articles': build_articles_snapshot(articles)}


@app.post('/api/v1/publishing/jobs', response_model=PublishJobOut)
def create_publish_job(payload: PublishJobRequest, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    settings_obj = get_or_create_settings(db)
    period_start = None
    period_end = None
    planned_articles_per_site = None
    selected_site_ids = payload.selected_site_ids or None

    if selected_site_ids:
        existing_site_ids = set(db.execute(select(WordPressSite.id).where(WordPressSite.id.in_(selected_site_ids))).scalars().all())
        missing_site_ids = [site_id for site_id in selected_site_ids if site_id not in existing_site_ids]
        if missing_site_ids:
            raise HTTPException(status_code=400, detail=f'Unknown WordPress site ids: {missing_site_ids}')

    if payload.pipeline_type == 'continuous':
        snapshot = []
    elif payload.pipeline_type == 'retrospective':
        if not payload.period_start or not payload.period_end or not payload.articles_per_day:
            raise HTTPException(status_code=400, detail='period_start, period_end and articles_per_day are required for retrospective jobs')
        try:
            days = iter_days(payload.period_start, payload.period_end)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        snapshot = build_retrospective_snapshot(db, payload.country_code, payload.period_start, payload.period_end, payload.articles_per_day)
        empty_days = [day['date'] for day in snapshot['days'] if not day['articles']]
        if empty_days:
            raise HTTPException(status_code=400, detail=f'No archived articles found for days: {", ".join(empty_days[:10])}')
        period_start = datetime.combine(payload.period_start, datetime.min.time(), tzinfo=timezone.utc)
        period_end = datetime.combine(payload.period_end, datetime.max.time(), tzinfo=timezone.utc)
        planned_articles_per_site = len(days) * payload.articles_per_day
    else:
        articles = select_recent_articles(db, payload.country_code, payload.hours_back)
        if not articles:
            raise HTTPException(status_code=400, detail='No recent articles found for selected country and period')
        snapshot = build_articles_snapshot(articles)

    job = PublishJob(
        country_code=payload.country_code.upper(),
        country_name=payload.country_name,
        target_language=payload.target_language or settings_obj.default_language,
        hours_back=payload.hours_back,
        pipeline_type=payload.pipeline_type,
        period_start=period_start,
        period_end=period_end,
        articles_per_day=payload.articles_per_day,
        planned_articles_per_site=planned_articles_per_site,
        site_limit=payload.site_limit,
        selected_site_ids=selected_site_ids,
        rewrite_model=payload.rewrite_model or settings_obj.rewrite_model,
        image_model=payload.image_model or settings_obj.image_model,
        stop_words=payload.stop_words or settings_obj.stop_words,
        specificity=payload.specificity or settings_obj.specificity,
        articles_snapshot=snapshot,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@app.get('/api/v1/publishing/jobs', response_model=list[PublishJobOut])
def list_publish_jobs(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), _: None = Depends(require_auth)):
    return list(db.execute(select(PublishJob).order_by(PublishJob.created_at.desc()).limit(limit)).scalars().all())


@app.post('/api/v1/publishing/jobs/{job_id}/cancel', response_model=PublishJobOut)
def cancel_publish_job(job_id: int, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    job = db.get(PublishJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    if job.status in {'completed', 'completed_with_errors', 'failed'}:
        raise HTTPException(status_code=400, detail=f'Job with status {job.status} cannot be canceled')
    job.status = 'canceled'
    job.retry_after = None
    job.finished_at = now_utc()
    job.error_text = 'Canceled by admin'
    db.execute(
        update(PublishedArticle)
        .where(PublishedArticle.job_id == job.id)
        .where(PublishedArticle.status.in_(['queued', 'running']))
        .values(status='canceled', error_text='Canceled by admin')
    )
    db.commit()
    db.refresh(job)
    return job


@app.post('/api/v1/publishing/jobs/{job_id}/restart', response_model=PublishJobOut)
def restart_publish_job(job_id: int, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    job = db.get(PublishJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    if job.status == 'running':
        raise HTTPException(status_code=400, detail='Cancel the running job before restart')
    db.execute(
        update(PublishedArticle)
        .where(PublishedArticle.job_id == job.id)
        .where(PublishedArticle.status.in_(['queued', 'running', 'failed', 'canceled']))
        .values(status='canceled', error_text='Canceled before job restart')
    )
    job.status = 'queued'
    job.retry_after = None
    job.started_at = None
    job.finished_at = None
    job.error_text = None
    db.commit()
    db.refresh(job)
    return job


@app.get('/api/v1/publishing/jobs/{job_id}/articles', response_model=list[PublishedArticleOut])
def list_published_articles(job_id: int, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    return list(db.execute(select(PublishedArticle).where(PublishedArticle.job_id == job_id).order_by(PublishedArticle.id.asc())).scalars().all())
