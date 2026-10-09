"""Hàng đợi job trên Postgres (bảng jobs) — không thêm Redis/Celery.

Trạng thái (C1): queued -> processing -> done | blocked | error.
    blocked = cần người xử lý (file không parse được, nội dung nghi injection, agent verifier chặn...)
              — KHÔNG tự retry; admin xử lý rồi requeue (docs/OPERATIONS.md).
    error   = lỗi hệ thống — tự retry với backoff tới max_attempts, hết lượt thì dừng ở error.
Claim job: SELECT ... FOR UPDATE SKIP LOCKED -> nhiều worker chạy song song không giành nhau.
Idempotent: enqueue với idempotency_key đã tồn tại -> trả job cũ, không tạo trùng.
Job processing quá stale_lock_minutes (worker chết giữa chừng) -> requeue tự động.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session

from core.db import session_scope
from core.i18n import get_lang
from core.logging import log_event
from core.models import Job, utcnow

logger = logging.getLogger(__name__)

JOB_TYPES = ("ingest_file", "reindex", "graph_sync", "graph_rebuild", "document_lifecycle", "knowledge_extract", "report_generate",
             "model_analyze", "model_build", "relations_discover")


class JobBlocked(Exception):
    """Handler báo job cần người xử lý — chuyển trạng thái blocked, không retry."""

    def __init__(self, reason: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.result = result or {}


def enqueue_job(session: Session, job_type: str, payload: dict[str, Any], *, idempotency_key: str | None = None,
                document_id: str | None = None, batch_id: str | None = None, created_by: str | None = None,
                max_attempts: int = 3, delay_seconds: float = 0, workspace_id: str | None = None) -> Job:
    if job_type not in JOB_TYPES:
        raise ValueError(f"job type không hợp lệ: {job_type}")
    if idempotency_key:
        existing = session.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            return existing
    # ngôn ngữ giao diện của người tạo job -> worker chạy job trong use_lang() (thông báo, báo cáo, lỗi job)
    payload = {**payload, "lang": payload.get("lang") or get_lang()}
    job = Job(type=job_type, payload=payload, idempotency_key=idempotency_key, document_id=document_id,
              batch_id=batch_id, created_by=created_by, max_attempts=max_attempts, workspace_id=workspace_id,
              run_after=utcnow() + timedelta(seconds=delay_seconds))
    session.add(job)
    session.flush()
    log_event(logger, "job_enqueued", job_id=job.id, job_type=job_type, document_id=document_id)
    return job


def claim_next(session: Session, worker_id: str, types: tuple[str, ...] = JOB_TYPES) -> Job | None:
    row = session.execute(
        select(Job).where(Job.status == "queued", Job.run_after <= utcnow(), Job.type.in_(types))
        .order_by(Job.created_at).limit(1).with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status, row.locked_by, row.locked_at = "processing", worker_id, utcnow()
    row.started_at = row.started_at or utcnow()
    row.attempts += 1
    session.flush()
    return row


def update_progress(job_id: str, done: int, total: int | None = None, note: str | None = None) -> None:
    """Ghi tiến độ bằng transaction RIÊNG để client thấy ngay khi job còn đang chạy."""
    with session_scope() as s:
        values: dict[str, Any] = {"progress_done": done, "locked_at": utcnow()}
        if total is not None:
            values["progress_total"] = total
        if note:
            s.execute(text("UPDATE jobs SET result = jsonb_set(coalesce(result, '{}'::jsonb), '{note}', to_jsonb(CAST(:n AS text))) "
                           "WHERE id = :id"), {"n": note, "id": job_id})
        s.execute(update(Job).where(Job.id == job_id).values(**values))


def complete(session: Session, job: Job, result: dict[str, Any]) -> None:
    job.status, job.result, job.error, job.finished_at = "done", result, None, utcnow()
    job.locked_by = None
    if job.progress_total and job.progress_done < job.progress_total:
        job.progress_done = job.progress_total
    log_event(logger, "job_done", job_id=job.id, job_type=job.type)


def block(session: Session, job: Job, reason: str, result: dict[str, Any] | None = None) -> None:
    job.status, job.error, job.finished_at, job.locked_by = "blocked", reason, utcnow(), None
    job.result = {**(job.result or {}), **(result or {}), "blocked_reason": reason}
    log_event(logger, "job_blocked", level=logging.WARNING, job_id=job.id, job_type=job.type, reason=reason)


def fail(session: Session, job: Job, error: str) -> None:
    job.locked_by = None
    if job.attempts < job.max_attempts:
        delay = min(300, 15 * (2 ** (job.attempts - 1)))
        job.status, job.error, job.run_after = "queued", error, utcnow() + timedelta(seconds=delay)
        log_event(logger, "job_retry_scheduled", level=logging.WARNING, job_id=job.id, attempt=job.attempts,
                  max_attempts=job.max_attempts, delay_seconds=delay, error=error[:500])
    else:
        job.status, job.error, job.finished_at = "error", error, utcnow()
        log_event(logger, "job_failed", level=logging.ERROR, job_id=job.id, job_type=job.type, error=error[:500])


def requeue(session: Session, job_id: str, *, actor: str | None = None) -> Job:
    """Admin chạy lại job blocked/error (sau khi đã xử lý nguyên nhân)."""
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(job_id)
    job.status, job.error, job.run_after, job.finished_at = "queued", None, utcnow(), None
    job.attempts, job.alerted = 0, False
    job.result = {**(job.result or {}), "requeued_by": actor}
    return job


def recover_stale(session: Session, stale_minutes: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    res = session.execute(update(Job).where(Job.status == "processing", Job.locked_at < cutoff)
                          .values(status="queued", locked_by=None, error="worker mất kết nối giữa chừng — tự requeue"))
    if res.rowcount:
        log_event(logger, "jobs_recovered_stale", level=logging.WARNING, count=res.rowcount)
    return res.rowcount or 0


def job_view(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.id, "type": job.type, "status": job.status, "document_id": job.document_id, "workspace_id": job.workspace_id,
        "batch_id": job.batch_id, "attempts": job.attempts, "max_attempts": job.max_attempts,
        "progress": {"done": job.progress_done, "total": job.progress_total,
                     "percent": round(100 * job.progress_done / job.progress_total, 1) if job.progress_total else None},
        "error": job.error, "result": job.result, "payload": job.payload,
        "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at,
    }


def queue_stats(session: Session) -> dict[str, int]:
    rows = session.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all()
    return {status: int(n) for status, n in rows}


__all__ = ["enqueue_job", "claim_next", "update_progress", "complete", "block", "fail", "requeue", "recover_stale",
           "job_view", "queue_stats", "JobBlocked", "and_", "or_"]
