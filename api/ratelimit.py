"""Rate limit theo key (D2) — cửa sổ cố định 1 phút, bộ đếm trong Postgres (rate_limit_counters) nên
đúng cả khi chạy nhiều replica api. Vượt -> 429 kèm Retry-After. Bộ đếm cũ dọn bởi job retention."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.errors import RateLimited
from core.i18n import L


def check_rate_limit(session: Session, key: str, limit_per_minute: int) -> int:
    if limit_per_minute <= 0:
        return 0
    now = datetime.now(timezone.utc)
    window = now.replace(second=0, microsecond=0)
    count = session.execute(text(
        "INSERT INTO rate_limit_counters (key, window_start, count) VALUES (:k, :w, 1) "
        "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit_counters.count + 1 RETURNING count"),
        {"k": key, "w": window}).scalar_one()
    session.commit()
    if count > limit_per_minute:
        retry = 60 - now.second
        raise RateLimited(L(f"Vượt giới hạn {limit_per_minute} request/phút, thử lại sau {retry}s.", f"Rate limit of {limit_per_minute} requests/minute exceeded, try again in {retry}s."),
                          context={"retry_after": retry, "key": key})
    return int(count)
