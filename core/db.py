"""Engine/session SQLAlchemy (psycopg 3) dùng chung cho api + worker + CLI.

Bảng KHÔNG BAO GIỜ tạo bằng tay hay bằng metadata.create_all() ở runtime — chỉ qua Alembic
(migrations/). Module này chỉ cấp engine/session và vài tiện ích transaction.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    engine = create_engine(
        url, pool_pre_ping=True, pool_size=10, max_overflow=10, pool_recycle=1800, future=True,
    )

    @event.listens_for(engine, "connect")
    def _register_vector(dbapi_conn, _record):  # pragma: no cover - cần DB thật
        try:
            from pgvector.psycopg import register_vector

            register_vector(dbapi_conn)
        except Exception as e:  # extension chưa tạo (lần chạy migration đầu tiên) — không chặn
            logger.debug("register_vector bỏ qua: %s", e)
            try:
                dbapi_conn.rollback()
            except Exception:
                pass

    return engine


@lru_cache(maxsize=4)
def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Session có commit/rollback tự động — dùng cho worker/CLI. API dùng dependency get_db()."""
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_healthcheck(session: Session) -> bool:
    session.execute(text("SELECT 1"))
    return True


def reset_engine_cache() -> None:
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
