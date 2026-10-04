"""Log retention (E5) — worker chạy định kỳ (worker.retention_interval_hours).

- Trace QA (qa_traces) + trace Langfuse: giữ observability.trace_retention_days (mặc định 30 ngày).
  Langfuse self-host: xoá trực tiếp trong DB của Langfuse (LANGFUSE_DATABASE_URL) — bản OSS không tự
  dọn theo thời gian.
- Audit log: append-only, xoay vòng theo THÁNG (data/security/audit/audit_trail-YYYY-MM.log). Chỉ xoá
  NGUYÊN FILE của tháng đã quá observability.audit_retention_days (mặc định 365 ngày) — không bao giờ
  sửa/xoá từng dòng (giữ tính append-only).
- Abstention: observability.abstention_retention_days. Session chat nhàn rỗi: chat.session_idle_days.
- Rác vận hành: rate_limit_counters > 1 ngày, alert_events > 90 ngày, job done/blocked/error > 90 ngày.
"""
from __future__ import annotations

import glob
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from core.config import AppConfig, get_settings
from core.db import session_scope
from core.logging import log_event
from security.audit import AppendOnlyAuditLog

logger = logging.getLogger(__name__)

AUDIT_DIR = os.environ.get("AUDIT_LOG_DIR", os.path.join("data", "security", "audit"))
_MONTH_FILE_RE = re.compile(r"-(\d{4})-(\d{2})\.log$")


class RotatingAuditLog(AppendOnlyAuditLog):
    """AppendOnlyAuditLog ghi vào file theo tháng — retention xoá được cả file tháng cũ mà không phải
    sửa file đang ghi."""

    def __init__(self, directory: str | None = None, prefix: str = "audit_trail") -> None:
        self._dir = directory or AUDIT_DIR
        self._prefix = prefix
        super().__init__(path=self._current_path())

    def _current_path(self) -> str:
        return os.path.join(self._dir, f"{self._prefix}-{datetime.now(timezone.utc):%Y-%m}.log")

    def append(self, **kwargs: Any) -> Any:
        self._path = self._current_path()
        return super().append(**kwargs)

    def read_entries(self, run_id: str | None = None) -> list[dict]:
        out: list[dict] = []
        for path in sorted(glob.glob(os.path.join(self._dir, f"{self._prefix}-*.log"))):
            self._path = path
            out.extend(super().read_entries(run_id=run_id))
        self._path = self._current_path()
        return out


def monthly_gateway_log_path() -> str:
    return os.path.join(AUDIT_DIR, f"gateway-{datetime.now(timezone.utc):%Y-%m}.log")


def prune_audit_files(directory: str, retention_days: int, now: datetime | None = None) -> list[str]:
    """Xoá file audit của tháng có NGÀY CUỐI THÁNG cũ hơn retention. Thuần (test được với tmp dir)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    removed = []
    for path in glob.glob(os.path.join(directory, "*.log")):
        m = _MONTH_FILE_RE.search(path)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        month_end = datetime(y + (mo == 12), (mo % 12) + 1, 1, tzinfo=timezone.utc)
        if month_end < cutoff:
            os.remove(path)
            removed.append(path)
    return removed


def run_retention(cfg: AppConfig) -> dict[str, Any]:
    obs = cfg.observability
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    statements = {
        "qa_traces": ("DELETE FROM qa_traces WHERE created_at < :c", now - timedelta(days=obs.trace_retention_days)),
        "llm_usage": ("DELETE FROM llm_usage WHERE created_at < :c", now - timedelta(days=max(obs.trace_retention_days, 90))),
        "abstentions": ("DELETE FROM abstentions WHERE created_at < :c", now - timedelta(days=obs.abstention_retention_days)),
        "chat_sessions": ("DELETE FROM chat_sessions WHERE updated_at < :c", now - timedelta(days=cfg.chat.session_idle_days)),
        "rate_limit_counters": ("DELETE FROM rate_limit_counters WHERE window_start < :c", now - timedelta(days=1)),
        "alert_events": ("DELETE FROM alert_events WHERE sent_at < :c", now - timedelta(days=90)),
        "jobs": ("DELETE FROM jobs WHERE status IN ('done','blocked','error') AND finished_at < :c", now - timedelta(days=90)),
    }
    with session_scope() as s:
        for name, (sql, cutoff) in statements.items():
            out[name] = s.execute(text(sql), {"c": cutoff}).rowcount or 0
    out["langfuse"] = _prune_langfuse(now - timedelta(days=obs.trace_retention_days))
    out["audit_files_removed"] = prune_audit_files(AUDIT_DIR, obs.audit_retention_days, now)
    log_event(logger, "retention_done", **{k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})
    return out


def _prune_langfuse(cutoff: datetime) -> dict[str, Any] | str:
    url = get_settings().langfuse_database_url
    if not url:
        return "skipped (LANGFUSE_DATABASE_URL chưa cấu hình)"
    import psycopg

    result: dict[str, Any] = {}
    try:
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True, connect_timeout=5) as conn:
            for table, col in (("scores", "timestamp"), ("observations", "start_time"), ("traces", "timestamp")):
                try:
                    cur = conn.execute(f'DELETE FROM {table} WHERE "{col}" < %s', [cutoff])
                    result[table] = cur.rowcount
                except Exception as e:  # schema Langfuse khác phiên bản -> báo rõ, không dừng các bảng khác
                    result[table] = f"lỗi: {e}"[:200]
    except Exception as e:
        logger.warning("không kết nối được DB Langfuse để dọn trace: %s", e)
        return f"lỗi kết nối: {e}"[:200]
    return result
