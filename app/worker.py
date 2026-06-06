from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal, init_db
from .models import Article, FetchCommand, FetchRun, FetchSourceLog, Source
from .rss_fetcher import SourceFetchResult, fetch_sources_parallel
from .utils import now_utc


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
            await asyncio.sleep(5)
            slept += 5


if __name__ == '__main__':
    asyncio.run(main_loop())
