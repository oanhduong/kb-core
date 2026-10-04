"""Ingest endpoints: POST /ingest (upload 1 hoặc nhiều file -> job_id), GET /ingest/{job_id}, danh sách
job, requeue job blocked/error. Xử lý thật chạy ở worker (không block request)."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import require_role
from api.deps import audit_action, get_db, get_services
from core.errors import NotFoundError, ValidationFailed
from core.models import Job, new_id
from core.principal import Principal
from ingest.service import UploadOptions, register_upload
from worker.queue import job_view, requeue

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", summary="Upload 1 hoặc nhiều file — mỗi file 1 job nền (queued -> processing -> done|blocked|error)")
def upload(request: Request, files: list[UploadFile] = File(..., description="Một hoặc nhiều file"),
           doc_type: str | None = Form(None), store_code: str | None = Form(None),
           effective_date: date | None = Form(None), title: str | None = Form(None), version: str | None = Form(None),
           series_key: str | None = Form(None), replace_mode: str = Form("document"),
           principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db),
           services: Any = Depends(get_services)) -> dict:
    if not files:
        raise ValidationFailed("Cần ít nhất 1 file")
    if title and len(files) > 1:
        raise ValidationFailed("title chỉ dùng khi upload 1 file")
    batch_id = new_id()
    out = []
    for f in files:
        opts = UploadOptions(title=title, doc_type=doc_type, store_code=store_code, effective_date=effective_date,
                             version=version, series_key=series_key, replace_mode=replace_mode)
        res = register_upload(db, services.cfg, src=f.file, filename=f.filename or "file", opts=opts,
                              uploaded_by=principal.subject, batch_id=batch_id)
        out.append({"job_id": res.job_id, "document_id": res.document_id, "filename": res.filename,
                    "duplicate_of_existing": res.duplicate_of_upload, "status": res.status})
        audit_action(request, principal, "ingest_upload", res.document_id, filename=res.filename, duplicate=res.duplicate_of_upload)
    db.commit()
    return {"batch_id": batch_id, "jobs": out, "job_id": out[0]["job_id"] if len(out) == 1 else None}


@router.get("", summary="Danh sách job (lọc trạng thái/loại)")
def list_jobs(status: str | None = None, type: str | None = None, batch_id: str | None = None,
              limit: int = Query(50, le=500), principal: Principal = Depends(require_role("supervisor")),
              db: Session = Depends(get_db)) -> list[dict]:
    q = select(Job)
    if status:
        q = q.where(Job.status == status)
    if type:
        q = q.where(Job.type == type)
    if batch_id:
        q = q.where(Job.batch_id == batch_id)
    return [job_view(j) for j in db.execute(q.order_by(Job.created_at.desc()).limit(limit)).scalars()]


@router.get("/{job_id}", summary="Trạng thái job (progress, kết quả, lý do blocked/error)")
def get_job(job_id: str, principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Không có job {job_id}")
    return job_view(job)


@router.post("/{job_id}/requeue", summary="Chạy lại job blocked/error sau khi đã xử lý nguyên nhân")
def requeue_job(job_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Không có job {job_id}")
    if job.status not in ("blocked", "error"):
        raise ValidationFailed(f"Chỉ requeue được job blocked/error (hiện: {job.status})")
    requeue(db, job_id, actor=principal.subject)
    audit_action(request, principal, "job_requeue", job_id)
    return job_view(db.get(Job, job_id))
