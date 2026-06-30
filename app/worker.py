from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal, init_db
from .models import Article, FetchCommand, FetchRun, FetchSourceLog, Source
from .rss_fetcher import SourceFetchResult, fetch_sources_parallel
from .utils import now_utc


def retrospective_scheduled_for(day: date | str) -> datetime:
    if isinstance(day, str):
        day = datetime.fromisoformat(day).date()
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _load_sources(db: Session, country_code: str | None = None) -> list[Source]:
    stmt = select(Source).where(Source.is_active.is_(True), Source.rss_url.is_not(None)).order_by(Source.id.asc())
    if country_code:
        stmt = stmt.where(Source.country_code == country_code.upper())
    return list(db.execute(stmt).scalars().all())


def _save_source_result(db: Session, run_id: int, source: Source, result: SourceFetchResult, known_hashes: set[str]) -> tuple[int, int]:
    started = now_utc()
    new_count = 0
    duplicate_count = 0

    if result.ok:
        for item in result.articles:
            # Дедупликация должна работать не только против уже сохранённой базы,
            # но и против материалов, уже добавленных в текущем проходе до commit().
            # Иначе при autoflush=False два одинаковых url_hash из разных источников
            # или из одного RSS падают на UNIQUE(ix_articles_url_hash) при commit().
            if not item.url_hash or item.url_hash in known_hashes:
                duplicate_count += 1
                continue

            stmt = pg_insert(Article).values(
                source_id=source.id,
                country_code=source.country_code,
                country_name=source.country_name,
                source_name=source.source_name,
                source_type=source.source_type,
                url=item.url,
                canonical_url=item.canonical_url,
                title=item.title,
                summary=item.summary,
                content=item.content,
                author=item.author,
                language=item.language,
                published_at=item.published_at,
                fetched_at=now_utc(),
                content_hash=item.content_hash,
                url_hash=item.url_hash,
                raw_json=item.raw_json,
            ).on_conflict_do_nothing(index_elements=['url_hash'])
            insert_result = db.execute(stmt)
            known_hashes.add(item.url_hash)
            if insert_result.rowcount == 1:
                new_count += 1
            else:
                duplicate_count += 1

        source.last_fetch_at = now_utc()
        source.last_success_at = now_utc()
        source.last_error_text = None
        source.last_http_status = result.http_status
        status = 'ok'
        error_text = None
    else:
        source.last_fetch_at = now_utc()
        source.last_error_at = now_utc()
        source.last_error_text = result.error
        source.last_http_status = result.http_status
        status = 'failed'
        error_text = result.error

    db.add(FetchSourceLog(
        run_id=run_id,
        source_id=source.id,
        source_name=source.source_name,
        rss_url=source.rss_url,
        started_at=started,
        finished_at=now_utc(),
        status=status,
        http_status=result.http_status,
        error_text=error_text,
        articles_seen=len(result.articles),
        articles_new=new_count,
        articles_duplicate=duplicate_count,
    ))
    return new_count, duplicate_count


async def run_fetch_pass(country_code: str | None = None) -> int:
    run_start = now_utc()
    with SessionLocal() as db:
        sources = _load_sources(db, country_code)
        run = FetchRun(
            started_at=run_start,
            status='running',
            sources_total=len(sources),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    if not sources:
        with SessionLocal() as db:
            run = db.get(FetchRun, run_id)
            if run:
                run.status = 'completed'
                run.finished_at = now_utc()
                run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
                db.commit()
        return run_id

    results = await fetch_sources_parallel(sources, max(1, settings.fetch_concurrency))

    with SessionLocal() as db:
        sources_by_id = {s.id: s for s in db.execute(select(Source).where(Source.id.in_([s.id for s in sources]))).scalars().all()}
        run = db.get(FetchRun, run_id)
        sources_ok = 0
        sources_failed = 0
        articles_new = 0
        articles_duplicate = 0

        candidate_hashes = sorted({
            item.url_hash
            for result in results
            if result.ok
            for item in result.articles
            if item.url_hash
        })
        known_hashes: set[str] = set()
        # Грузим уже существующие хэши чанками, чтобы не создавать огромный IN (...)
        # на больших проходах. Новые хэши текущего прохода добавляются в этот же set.
        chunk_size = 1000
        for i in range(0, len(candidate_hashes), chunk_size):
            chunk = candidate_hashes[i:i + chunk_size]
            existing = db.execute(select(Article.url_hash).where(Article.url_hash.in_(chunk))).scalars().all()
            known_hashes.update(existing)

        for result in results:
            source = sources_by_id.get(result.source_id)
            if not source:
                continue
            new_count, duplicate_count = _save_source_result(db, run_id, source, result, known_hashes)
            if result.ok:
                sources_ok += 1
            else:
                sources_failed += 1
            articles_new += new_count
            articles_duplicate += duplicate_count

        if run:
            run.status = 'completed'
            run.finished_at = now_utc()
            run.sources_ok = sources_ok
            run.sources_failed = sources_failed
            run.articles_new = articles_new
            run.articles_duplicate = articles_duplicate
            run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if run:
                run.status = 'failed'
                run.finished_at = now_utc()
                run.error_text = f'IntegrityError while saving fetch pass: {exc}'
                run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
                db.commit()
            raise

    return run_id


def _claim_command(db: Session) -> FetchCommand | None:
    command = db.execute(
        select(FetchCommand)
        .where(FetchCommand.status == 'queued')
        .order_by(FetchCommand.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if not command:
        return None
    command.status = 'running'
    command.started_at = now_utc()
    db.commit()
    db.refresh(command)
    return command


async def process_commands_once() -> bool:
    with SessionLocal() as db:
        command = _claim_command(db)
    if not command:
        return False

    try:
        run_id = await run_fetch_pass(command.country_code)
        with SessionLocal() as db:
            cmd = db.get(FetchCommand, command.id)
            if cmd:
                cmd.status = 'completed'
                cmd.finished_at = now_utc()
                cmd.run_id = run_id
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            cmd = db.get(FetchCommand, command.id)
            if cmd:
                cmd.status = 'failed'
                cmd.finished_at = now_utc()
                cmd.error_text = repr(exc)
                db.commit()
    return True


async def main_loop() -> None:
    init_db()
    print('RSS worker started')
    while True:
        # Manual commands have priority.
        while await process_commands_once():
            pass
        while await process_publish_jobs_once():
            pass

        print('Starting scheduled full fetch pass')
        try:
            run_id = await run_fetch_pass()
            print(f'Fetch pass completed: run_id={run_id}')
        except Exception as exc:
            print(f'Fetch pass failed: {exc!r}')

        print(f'Sleeping {settings.fetch_interval_seconds} seconds')
        slept = 0
        # During sleep, poll manual commands without starting a new scheduled pass.
        while slept < settings.fetch_interval_seconds:
            if await process_commands_once():
                continue
            if await process_publish_jobs_once():
                continue
            await asyncio.sleep(5)
            slept += 5

async def process_publish_jobs_once() -> bool:
    from .models import PublishedArticle, PublishingSettings, PublishJob, WordPressSite
    from .publishing import DEFAULT_ROUTERAI_IMAGE_MODEL, build_image_prompt, build_rewrite_messages, get_or_create_settings, mark_source_articles_used, published_slot_exists, routerai_chat, routerai_image, site_generation_delay, source_ids_from_snapshot, upload_to_wordpress

    with SessionLocal() as db:
        job = db.execute(select(PublishJob).where((PublishJob.status == 'queued') | ((PublishJob.status == 'rate_limited') & ((PublishJob.retry_after.is_(None)) | (PublishJob.retry_after <= now_utc())))).order_by(PublishJob.created_at.asc()).limit(1)).scalar_one_or_none()
        if not job:
            return False
        job.status = 'running'
        job.retry_after = None
        job.started_at = job.started_at or now_utc()
        db.commit()
        db.refresh(job)
        settings_obj = get_or_create_settings(db)
        site_stmt = select(WordPressSite).where(WordPressSite.is_active.is_(True)).order_by(WordPressSite.id.asc())
        if job.site_limit:
            site_stmt = site_stmt.limit(job.site_limit)
        sites = list(db.execute(site_stmt).scalars().all())
        api_key = settings_obj.routerai_api_key
        base_url = settings_obj.routerai_base_url or 'https://routerai.ru/api/v1'

    if not api_key:
        with SessionLocal() as db:
            current = db.get(PublishJob, job.id)
            if current:
                current.status = 'failed'
                current.finished_at = now_utc()
                current.error_text = 'RouterAI API key is not configured'
                db.commit()
        return True
    if not sites:
        with SessionLocal() as db:
            current = db.get(PublishJob, job.id)
            if current:
                current.status = 'failed'
                current.finished_at = now_utc()
                current.error_text = 'No active WordPress sites configured'
                db.commit()
        return True

    async def publish_one(site: WordPressSite, snapshot: list[dict], scheduled_for: datetime | None, sequence_number: int | None) -> tuple[str, datetime | None]:
        source_article_ids = source_ids_from_snapshot(snapshot)
        row_id = None
        with SessionLocal() as db:
            if published_slot_exists(db, job.id, site.id, scheduled_for, sequence_number):
                return 'skipped', None
            retry_at = None if job.pipeline_type == 'retrospective' else site_generation_delay(db, site)
            if retry_at:
                return 'rate_limited', retry_at
            current_job = db.get(PublishJob, job.id)
            if current_job and current_job.status == 'canceled':
                return 'canceled', None
            row = PublishedArticle(job_id=job.id, site_id=site.id, status='running', scheduled_for=scheduled_for, sequence_number=sequence_number, source_article_ids=source_article_ids)
            db.add(row)
            db.commit()
            db.refresh(row)
            row_id = row.id
        try:
            with SessionLocal() as db:
                current_job = db.get(PublishJob, job.id)
                if current_job and current_job.status == 'canceled':
                    row = db.get(PublishedArticle, row_id)
                    if row:
                        row.status = 'canceled'
                        db.commit()
                    return 'canceled', None
            planned_date = scheduled_for.date().isoformat() if scheduled_for else None
            article = await routerai_chat(
                base_url,
                api_key,
                job.rewrite_model or 'gpt-4o-mini',
                build_rewrite_messages(snapshot, site, job, planned_date=planned_date, sequence_number=sequence_number),
            )
            title = article.get('title') or 'Generated article'
            image_prompt = build_image_prompt(title, job.country_name or job.country_code, job.country_code)
            image = await routerai_image(base_url, api_key, job.image_model or DEFAULT_ROUTERAI_IMAGE_MODEL, image_prompt)
            with SessionLocal() as db:
                current_job = db.get(PublishJob, job.id)
                if current_job and current_job.status == 'canceled':
                    row = db.get(PublishedArticle, row_id)
                    if row:
                        row.status = 'canceled'
                        db.commit()
                    return 'canceled', None
            wp = await upload_to_wordpress(site, article, image, image_prompt, publish_at=scheduled_for)
            with SessionLocal() as db:
                row = db.get(PublishedArticle, row_id)
                if row and row.status != 'canceled':
                    row.status = 'published'
                    row.title = title
                    row.slug = article.get('slug')
                    row.excerpt = article.get('excerpt')
                    row.content = article.get('content_html') or article.get('content')
                    row.seo_title = article.get('seo_title')
                    row.meta_description = article.get('meta_description')
                    row.category_ids = wp.get('categories')
                    row.image_prompt = image_prompt
                    row.wp_post_id = wp.get('post_id')
                    row.wp_media_id = wp.get('media_id')
                    row.wp_link = wp.get('link')
                    row.published_at = now_utc()
                    mark_source_articles_used(db, source_article_ids, job.id)
                    db.commit()
            return 'published', None
        except Exception as exc:
            with SessionLocal() as db:
                row = db.get(PublishedArticle, row_id)
                if row:
                    row.status = 'failed'
                    row.error_text = repr(exc)
                    db.commit()
            return 'failed', None

    had_errors = False
    retry_after = None
    if job.pipeline_type == 'retrospective' and isinstance(job.articles_snapshot, dict):
        articles_per_day = job.articles_per_day or job.articles_snapshot.get('articles_per_day') or 1
        for site in sites:
            for day in job.articles_snapshot.get('days', []):
                day_articles = day.get('articles') or []
                if not day_articles:
                    had_errors = True
                    continue
                for index in range(articles_per_day):
                    scheduled_for = retrospective_scheduled_for(day['date'])
                    rotated = day_articles[index % len(day_articles):] + day_articles[:index % len(day_articles)]
                    result, limited_until = await publish_one(site, rotated, scheduled_for, index + 1)
                    if result == 'rate_limited':
                        retry_after = limited_until
                        break
                    if result == 'canceled':
                        return True
                    had_errors = (result == 'failed') or had_errors
                if retry_after:
                    break
            if retry_after:
                break
    else:
        snapshot = job.articles_snapshot if isinstance(job.articles_snapshot, list) else []
        for site in sites:
            result, limited_until = await publish_one(site, snapshot, None, None)
            if result == 'rate_limited':
                retry_after = limited_until
                break
            if result == 'canceled':
                return True
            had_errors = (result == 'failed') or had_errors

    with SessionLocal() as db:
        current = db.get(PublishJob, job.id)
        if current:
            if retry_after:
                current.status = 'rate_limited'
                current.retry_after = retry_after
                current.error_text = f'Publishing paused until {retry_after.isoformat()} because a site generation limit was reached'
            else:
                current.status = 'completed_with_errors' if had_errors else 'completed'
                current.finished_at = now_utc()
            db.commit()
    return True


if __name__ == '__main__':
    asyncio.run(main_loop())
