"""Báo cáo theo workspace: tạo (từ prompt -> job sinh nền, hoặc rỗng), sửa block, ghim kết quả chat (trace) vào
báo cáo, xuất PDF/DOCX/XLSX."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db
from core.errors import NotFoundError, ValidationFailed
from core.models import Job, QATrace, Report, Workspace, new_id, utcnow
from core.principal import WorkspaceContext
from reports import export as rexport
from worker.queue import enqueue_job, job_view

router = APIRouter(prefix="/w/{workspace_id}/reports", tags=["reports"])


def _view(r: Report, job: Job | None = None) -> dict[str, Any]:
    return {"id": r.id, "title": r.title, "description": r.description, "status": r.status, "prompt": r.prompt, "blocks": r.blocks,
            "block_count": len(r.blocks or []), "created_by": r.created_by, "created_at": r.created_at, "updated_at": r.updated_at,
            "job": job_view(job) if job else None}


def _get(db: Session, ctx: WorkspaceContext, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None or r.workspace_id != ctx.workspace_id or r.deleted_at is not None:
        raise NotFoundError("Không có báo cáo")
    return r


@router.get("")
def list_reports(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db), limit: int = Query(50, le=200)) -> list[dict]:
    rows = db.execute(select(Report).where(Report.workspace_id == ctx.workspace_id, Report.deleted_at.is_(None))
                      .order_by(Report.updated_at.desc()).limit(limit)).scalars().all()
    return [{**_view(r), "blocks": None} for r in rows]


class ReportCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    prompt: str | None = Field(default=None, max_length=4000, description="Có prompt -> sinh nội dung tự động (job nền)")
    description: str | None = Field(default=None, max_length=2000)


@router.post("", summary="Tạo báo cáo: có prompt -> AI lập dàn ý + sinh từng mục (job nền); không -> báo cáo trống để ghim block")
def create_report(body: ReportCreate, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    if not body.title and not body.prompt:
        raise ValidationFailed("Cần title hoặc prompt")
    r = Report(workspace_id=ctx.workspace_id, title=body.title or body.prompt[:120], prompt=body.prompt, description=body.description,
               created_by=ctx.subject, status="generating" if body.prompt else "draft", blocks=[])
    db.add(r)
    db.flush()
    job = None
    if body.prompt:
        job = enqueue_job(db, "report_generate", {"report_id": r.id}, created_by=ctx.subject, workspace_id=ctx.workspace_id)
        r.job_id = job.id
    audit_action(request, ctx.principal, "report_create", r.id, generated=bool(body.prompt))
    return _view(r, job)


@router.get("/{report_id}")
def get_report(report_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    r = _get(db, ctx, report_id)
    return _view(r, db.get(Job, r.job_id) if r.job_id else None)


class ReportPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    blocks: list[dict[str, Any]] | None = Field(default=None, description="Thay toàn bộ danh sách block (sắp xếp/sửa/xoá)")


@router.patch("/{report_id}")
def patch_report(report_id: str, body: ReportPatch, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    r = _get(db, ctx, report_id)
    if body.title is not None:
        r.title = body.title.strip() or r.title
    if body.description is not None:
        r.description = body.description
    if body.blocks is not None:
        if len(body.blocks) > 500:
            raise ValidationFailed("Quá 500 block")
        for b in body.blocks:
            b.setdefault("id", new_id())
            if b.get("type") not in ("heading", "markdown", "chart", "table", "kpi", "sources"):
                raise ValidationFailed(f"Loại block không hợp lệ: {b.get('type')}")
        r.blocks = body.blocks
        if r.status == "draft" and body.blocks:
            r.status = "ready"
    r.updated_at = utcnow()
    audit_action(request, ctx.principal, "report_update", report_id, fields=sorted(body.model_dump(exclude_unset=True)))
    return _view(r)


class PinBody(BaseModel):
    trace_id: str = Field(min_length=8, max_length=32)
    heading: str | None = Field(default=None, max_length=200)
    include_answer: bool = True
    include_artifacts: bool = True


@router.post("/{report_id}/pin", summary="Ghim câu trả lời/biểu đồ của 1 lượt chat (trace_id) vào báo cáo")
def pin_trace(report_id: str, body: PinBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    r = _get(db, ctx, report_id)
    t = db.get(QATrace, body.trace_id)
    if t is None or t.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không có trace")
    if t.status not in ("answered", "answered_general"):
        raise ValidationFailed(f"Chỉ ghim được câu trả lời đã hoàn tất (trace đang {t.status})")
    blocks = list(r.blocks or [])
    if body.heading:
        blocks.append({"id": new_id(), "type": "heading", "text": body.heading, "level": 2})
    if body.include_answer:
        blocks.append({"id": new_id(), "type": "markdown", "text": t.answer or "", "citations": t.citations or [], "trace_id": t.trace_id,
                       "question": t.question})
    if body.include_artifacts:
        for art in t.artifacts or []:
            if art.get("type") != "query_result":
                continue
            chart = art.get("chart")
            if chart and chart.get("vega_lite"):
                blocks.append({"id": new_id(), "type": "chart", "title": chart.get("title") or art.get("purpose") or "", "chart": chart,
                               "sql": art.get("sql"), "citation": art.get("citation"), "trace_id": t.trace_id})
            elif art.get("rows"):
                blocks.append({"id": new_id(), "type": "table", "title": art.get("purpose") or "Số liệu", "columns": art["columns"],
                               "rows": art["rows"][:200], "sql": art.get("sql"), "citation": art.get("citation"), "trace_id": t.trace_id})
    r.blocks = blocks
    r.status = "ready" if r.status == "draft" else r.status
    r.updated_at = utcnow()
    audit_action(request, ctx.principal, "report_pin", report_id, trace_id=body.trace_id)
    return _view(r)


@router.post("/{report_id}/regenerate", summary="Sinh lại nội dung từ prompt (job nền)")
def regenerate(report_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    r = _get(db, ctx, report_id)
    if not r.prompt:
        raise ValidationFailed("Báo cáo không có prompt để sinh lại")
    job = enqueue_job(db, "report_generate", {"report_id": r.id}, created_by=ctx.subject, workspace_id=ctx.workspace_id)
    r.job_id, r.status = job.id, "generating"
    audit_action(request, ctx.principal, "report_regenerate", report_id)
    return _view(r, job)


@router.delete("/{report_id}")
def delete_report(report_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    r = _get(db, ctx, report_id)
    r.deleted_at = utcnow()
    audit_action(request, ctx.principal, "report_delete", report_id)
    return {"deleted": report_id}


@router.get("/{report_id}/export", summary="Xuất báo cáo: pdf | docx | xlsx | html")
def export_report(report_id: str, fmt: Literal["pdf", "docx", "xlsx", "html"] = "pdf", ctx: WorkspaceContext = Depends(workspace("viewer")),
                  db: Session = Depends(get_db)) -> Response:
    r = _get(db, ctx, report_id)
    ws = db.get(Workspace, ctx.workspace_id)
    meta = {"workspace_name": ws.name if ws else "", "generated_at": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")}
    base = r.title[:60].strip() or "bao_cao"
    ascii_name = "".join(ch for ch in unicodedata.normalize("NFKD", base) if ch.isascii() and (ch.isalnum() or ch in " _-")).strip() or "bao_cao"
    if fmt == "html":
        return Response(rexport.report_html(r.title, r.blocks or [], **meta), media_type="text/html; charset=utf-8")
    if fmt == "docx":
        data, media = rexport.export_docx(r.title, r.blocks or [], **meta), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "xlsx":
        data, media = rexport.export_xlsx(r.title, r.blocks or []), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        try:
            data, media = rexport.export_pdf(r.title, r.blocks or [], **meta), "application/pdf"
        except Exception as e:  # thiếu weasyprint/pango trên máy chủ -> báo rõ, gợi ý DOCX
            raise ValidationFailed(f"Không xuất được PDF trên máy chủ này ({type(e).__name__}). Hãy xuất DOCX hoặc HTML.") from e
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{ascii_name}.{fmt}"; filename*=UTF-8\'\'{quote(base)}.{fmt}'})
