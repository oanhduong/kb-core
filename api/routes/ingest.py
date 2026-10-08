"""Ingest theo workspace: POST /w/{ws}/ingest (editor), GET /w/{ws}/ingest/{job_id}, danh sách job, requeue."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db, get_services
from core.errors import NotFoundError, ValidationFailed
from core.i18n import L
from core.models import Job, new_id
from core.principal import WorkspaceContext
from ingest.service import UploadOptions, register_upload
from worker.queue import job_view, requeue

router = APIRouter(prefix="/w/{workspace_id}/ingest", tags=["ingest"])


@router.post("", summary="Upload 1 hoặc nhiều file — mỗi file 1 job nền (queued -> processing -> done|blocked|error)")
def upload(request: Request, files: list[UploadFile] = File(...), doc_type: str | None = Form(None),
           scope: str | None = Form(None, description="Pack retail: mã cửa hàng áp dụng"), effective_date: date | None = Form(None),
           title: str | None = Form(None), version: str | None = Form(None), series_key: str | None = Form(None),
           replace_mode: str = Form("document"), ctx: WorkspaceContext = Depends(workspace("editor")),
           db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    if not files:
        raise ValidationFailed(L("Cần ít nhất 1 file", "At least 1 file is required"))
    if title and len(files) > 1:
        raise ValidationFailed(L("title chỉ dùng khi upload 1 file", "title can only be used when uploading 1 file"))
    batch_id = new_id()
    out = []
    for f in files:
        opts = UploadOptions(title=title, doc_type=doc_type or None, store_code=scope or None, effective_date=effective_date,
                             version=version, series_key=series_key, replace_mode=replace_mode)
        res = register_upload(db, services.cfg, workspace_id=ctx.workspace_id, src=f.file, filename=f.filename or "file",
                              opts=opts, uploaded_by=ctx.subject, batch_id=batch_id)
        out.append({"job_id": res.job_id, "document_id": res.document_id, "filename": res.filename,
                    "duplicate_of_existing": res.duplicate_of_upload, "status": res.status})
        audit_action(request, ctx.principal, "ingest_upload", res.document_id, filename=res.filename, duplicate=res.duplicate_of_upload)
    db.commit()
    return {"batch_id": batch_id, "jobs": out, "job_id": out[0]["job_id"] if len(out) == 1 else None}


@router.get("", summary="Danh sách job của workspace")
def list_jobs(status: str | None = None, type: str | None = None, batch_id: str | None = None, document_id: str | None = None,
              limit: int = Query(50, le=500), ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(Job).where(Job.workspace_id == ctx.workspace_id)
    if status:
        q = q.where(Job.status == status)
    if type:
        q = q.where(Job.type == type)
    if batch_id:
        q = q.where(Job.batch_id == batch_id)
    if document_id:
        q = q.where(Job.document_id == document_id)
    return [job_view(j) for j in db.execute(q.order_by(Job.created_at.desc()).limit(limit)).scalars()]


def _job(db: Session, ctx: WorkspaceContext, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.workspace_id != ctx.workspace_id:
        raise NotFoundError(L(f"Không có job {job_id}", f"Job {job_id} not found"))
    return job


@router.get("/{job_id}", summary="Trạng thái job (progress, kết quả, lý do blocked/error)")
def get_job(job_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return job_view(_job(db, ctx, job_id))


@router.post("/{job_id}/requeue", summary="Chạy lại job blocked/error sau khi đã xử lý nguyên nhân")
def requeue_job(job_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    job = _job(db, ctx, job_id)
    if job.status not in ("blocked", "error"):
        raise ValidationFailed(L(f"Chỉ requeue được job blocked/error (hiện: {job.status})", f"Only blocked/error jobs can be requeued (current: {job.status})"))
    requeue(db, job_id, actor=ctx.subject)
    audit_action(request, ctx.principal, "job_requeue", job_id)
    return job_view(db.get(Job, job_id))
