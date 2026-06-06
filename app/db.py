import time
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401

    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            last_error = exc
            print(f"[db] PostgreSQL is not ready yet, attempt {attempt}/30: {exc}", flush=True)
            time.sleep(2)

    raise RuntimeError(f"PostgreSQL did not become available after 60 seconds: {last_error}")
