"""KB endpoints: health (C6), records (phân trang/lọc, sửa tay, xoá mềm/khôi phục — C4), documents
(xoá/khôi phục/duyệt file bị chặn), chunk (link citation), reindex (C5), conflicts (B4)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.auth import can_see_store, current_principal, require_role
from api.deps import audit_action, get_db, get_services
from core.errors import ForbiddenError, NotFoundError
from core.models import ChunkRow, Conflict, Document, KBRecordRow
from core.principal import Principal
from graph.store import GraphUnavailable
from kb import service as kb_service
from kb.chunking import embed_chunks
from kb.health import kb_health
from kb.lifecycle import get_document_or_404, get_record_or_404, restore_document, restore_record, soft_delete_document, soft_delete_record
from qa.citation import format_citation
from worker.queue import enqueue_job, job_view

router = APIRouter(tags=["kb"])


def _record_view(r: KBRecordRow, doc: Document | None = None) -> dict:
    return {"record_id": r.record_id, "document_id": r.document_id, "source_file": r.source_file,
            "source_type": r.source_type, "doc_type": r.doc_type, "location": r.location, "content": r.content,
            "store_code": r.store_code, "skus": r.skus, "category": r.category,
            "effective_date": r.effective_date, "valid_to": r.valid_to, "version": r.version, "status": r.status,
            "is_active": r.is_active, "duplicate_of": r.duplicate_of, "entity_key": r.entity_key,
            "confidence": r.confidence, "suspicious_content": r.suspicious_content, "metadata": r.meta,
            "manually_edited": r.manually_edited, "edited_by": r.edited_by, "edited_at": r.edited_at,
            "deleted_at": r.deleted_at, "updated_at": r.updated_at,
            "citation": format_citation(doc.title, doc.source_type, r.location) if doc else None}


def _doc_view(d: Document) -> dict:
    return {"document_id": d.id, "filename": d.filename, "title": d.title, "series_key": d.series_key,
            "source_type": d.source_type, "doc_type": d.doc_type, "store_code": d.store_code,
            "effective_date": d.effective_date, "valid_to": d.valid_to, "version": d.version, "status": d.status,
            "is_active": d.is_active, "replace_mode": d.replace_mode, "supersedes_id": d.supersedes_id,
            "superseded_by_id": d.superseded_by_id, "record_count": d.record_count, "size_bytes": d.size_bytes,
            "uploaded_by": d.uploaded_by, "created_at": d.created_at, "updated_at": d.updated_at, "deleted_at": d.deleted_at,
            "metadata": d.meta}


# ------------------------------------------------------------------ health
@router.get("/kb/health", summary="Sức khoẻ KB: record theo doc_type, file blocked, conflict mở, abstention 7 ngày, cập nhật gần nhất")
def health(days: int = Query(7, ge=1, le=90), principal: Principal = Depends(require_role("supervisor")),
           db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    graph_stats: Any = None
    try:
        store = services.graph_store()
        graph_stats = store.stats() if store else {"enabled": False}
    except GraphUnavailable as e:
        graph_stats = {"error": str(e)[:300]}
    return kb_health(db, services.cfg, days=days, graph_stats=graph_stats, embedding_model=services.embedder.model_name)


# ------------------------------------------------------------------ records
@router.get("/kb/records", summary="Duyệt record (phân trang, lọc doc_type/store/source_file/status/q)")
def list_records(doc_type: str | None = None, store_code: str | None = None, source_file: str | None = None,
                 document_id: str | None = None, status: str | None = None, sku: str | None = None,
                 q: str | None = Query(None, max_length=200), include_deleted: bool = False,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    query = select(KBRecordRow)
    if not include_deleted:
        query = query.where(KBRecordRow.deleted_at.is_(None))
    if doc_type:
        query = query.where(KBRecordRow.doc_type == doc_type)
    if principal.store_code:
        query = query.where(or_(KBRecordRow.store_code == principal.store_code, KBRecordRow.store_code.is_(None)))
    if store_code:
        query = query.where(KBRecordRow.store_code == store_code.upper())
    if source_file:
        query = query.where(KBRecordRow.source_file.ilike(f"%{source_file}%"))
    if document_id:
        query = query.where(KBRecordRow.document_id == document_id)
    if status:
        query = query.where(KBRecordRow.status == status)
    if sku:
        query = query.where(KBRecordRow.skus.any(sku.upper()))
    if q:
        query = query.where(KBRecordRow.content.ilike(f"%{q}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(KBRecordRow.updated_at.desc(), KBRecordRow.record_id)
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    docs = {d.id: d for d in db.execute(select(Document).where(Document.id.in_({r.document_id for r in rows}))).scalars()}
    return {"total": total, "page": page, "page_size": page_size, "items": [_record_view(r, docs.get(r.document_id)) for r in rows]}


@router.get("/kb/records/{record_id:path}/detail", summary="Chi tiết 1 record + chunk")
def get_record(record_id: str, principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    r = get_record_or_404(db, record_id)
    if not can_see_store(principal, r.store_code):
        raise ForbiddenError("Record thuộc cửa hàng khác")
    chunks = db.execute(select(ChunkRow).where(ChunkRow.record_id == record_id).order_by(ChunkRow.chunk_id)).scalars().all()
    return {**_record_view(r, db.get(Document, r.document_id)),
            "chunks": [{"chunk_id": c.chunk_id, "role": c.role, "text": c.text, "deleted": c.deleted} for c in chunks]}


class RecordPatch(BaseModel):
    content: str | None = Field(default=None, max_length=50000)
    store_code: str | None = None
    skus: list[str] | None = None
    category: str | None = None
    effective_date: date | None = None
    version: str | None = None
    doc_type: str | None = None
    metadata: dict | None = None
    is_active: bool | None = None


@router.patch("/kb/records/{record_id:path}", summary="Sửa tay record (khi extraction sai) — chunk/embedding/graph tự cập nhật")
def patch_record(record_id: str, body: RecordPatch, request: Request, principal: Principal = Depends(require_role("admin")),
                 db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    rec = get_record_or_404(db, record_id)
    changes = body.model_dump(exclude_unset=True)
    out = kb_service.edit_record(db, services.cfg, rec, changes, principal.subject)
    db.commit()
    emb = embed_chunks(services.sm, services.cfg, services.embedder, chunk_ids=None, document_ids=[rec.document_id])
    audit_action(request, principal, "record_edit", record_id, fields=sorted(changes), before=out["before"])
    return {**out, "embedding": emb, "record": _record_view(get_record_or_404(db, record_id))}


@router.delete("/kb/records/{record_id:path}", summary="Xoá mềm 1 record (khôi phục được)")
def delete_record(record_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                  db: Session = Depends(get_db)) -> dict:
    rec = soft_delete_record(db, get_record_or_404(db, record_id), principal.subject)
    enqueue_job(db, "graph_sync", {"document_id": rec.document_id, "llm": False}, document_id=rec.document_id, created_by=principal.subject)
    audit_action(request, principal, "record_delete", record_id)
    return _record_view(rec)


@router.post("/kb/records/{record_id:path}/restore", summary="Khôi phục record đã xoá mềm")
def restore_record_ep(record_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                      db: Session = Depends(get_db)) -> dict:
    rec = restore_record(db, get_record_or_404(db, record_id), principal.subject)
    enqueue_job(db, "graph_sync", {"document_id": rec.document_id, "llm": False}, document_id=rec.document_id, created_by=principal.subject)
    audit_action(request, principal, "record_restore", record_id)
    return _record_view(rec)


# ------------------------------------------------------------------ chunk (link citation)
@router.get("/kb/chunks/{chunk_id:path}", summary="Xem chunk gốc của 1 citation (+ parent, nguồn, vị trí)")
def get_chunk(chunk_id: str, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    c = db.get(ChunkRow, chunk_id)
    if c is None:
        raise NotFoundError("Không tìm thấy đoạn nguồn (có thể đã được reindex lại)")
    if not can_see_store(principal, c.store_code):
        raise ForbiddenError("Đoạn nguồn thuộc cửa hàng khác")
    doc = db.get(Document, c.document_id)
    rec = db.get(KBRecordRow, c.record_id)
    parent = db.get(ChunkRow, c.parent_chunk_id) if c.parent_chunk_id else None
    return {"chunk_id": c.chunk_id, "text": c.text, "parent_text": parent.text if parent else None,
            "record_content": rec.content if rec else None, "location": c.location, "role": c.role,
            "citation": format_citation(doc.title, doc.source_type, c.location) if doc else None,
            "document": {"document_id": doc.id, "title": doc.title, "filename": doc.filename, "doc_type": doc.doc_type,
                         "effective_date": doc.effective_date, "status": doc.status} if doc else None,
            "is_active": c.is_active, "effective_date": c.effective_date, "valid_to": c.valid_to, "store_code": c.store_code}


# ------------------------------------------------------------------ documents
@router.get("/kb/documents", summary="Danh sách file đã ingest")
def list_documents(status: str | None = None, doc_type: str | None = None, q: str | None = None, include_deleted: bool = False,
                   page: int = Query(1, ge=1), page_size: int = Query(50, le=200),
                   principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    query = select(Document)
    if not include_deleted:
        query = query.where(Document.deleted_at.is_(None))
    if status:
        query = query.where(Document.status == status)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if q:
        query = query.where(or_(Document.filename.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%")))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars()
    return {"total": total, "items": [_doc_view(d) for d in rows]}


@router.get("/kb/documents/{document_id}", summary="Chi tiết file")
def get_document(document_id: str, principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    from core.models import Job

    d = get_document_or_404(db, document_id)
    jobs = db.execute(select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc()).limit(20)).scalars()
    return {**_doc_view(d), "jobs": [job_view(j) for j in jobs]}


@router.delete("/kb/documents/{document_id}", summary="Xoá mềm cả file (bản cũ mà file này thay thế sẽ được khôi phục hiệu lực)")
def delete_document(document_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                    db: Session = Depends(get_db)) -> dict:
    out = soft_delete_document(db, get_document_or_404(db, document_id), principal.subject)
    ids = [document_id] + ([out["reactivated"]] if out.get("reactivated") else [])
    enqueue_job(db, "document_lifecycle", {"document_ids": ids}, document_id=document_id, created_by=principal.subject)
    audit_action(request, principal, "document_delete", document_id, reactivated=out.get("reactivated"))
    return out


@router.post("/kb/documents/{document_id}/restore", summary="Khôi phục file đã xoá mềm")
def restore_document_ep(document_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                        db: Session = Depends(get_db)) -> dict:
    out = restore_document(db, get_document_or_404(db, document_id), principal.subject)
    ids = [document_id, *out.get("superseded_documents", [])]
    enqueue_job(db, "document_lifecycle", {"document_ids": ids}, document_id=document_id, created_by=principal.subject)
    audit_action(request, principal, "document_restore", document_id)
    return out


@router.post("/kb/documents/{document_id}/approve", summary="Duyệt file bị chặn (nghi injection) sau khi đã kiểm tra nội dung")
def approve_document(document_id: str, request: Request, principal: Principal = Depends(require_role("admin")),
                     db: Session = Depends(get_db)) -> dict:
    from ingest.pipeline import approve_blocked_document

    doc = get_document_or_404(db, document_id)
    if doc.status != "blocked":
        raise ForbiddenError(f"Chỉ duyệt được file đang blocked (hiện: {doc.status})")
    out = approve_blocked_document(db, doc, principal.subject)
    audit_action(request, principal, "document_approve", document_id, records=out.get("approved_records"))
    return out


class ReindexBody(BaseModel):
    doc_type: str | None = None
    document_id: str | None = None
    rechunk: Literal["auto", "true", "false"] = "auto"
    model: str | None = Field(default=None, description="Embed trước cho model khác (chuẩn bị đổi model)")
    dim: int | None = None
    force: bool = False


@router.post("/kb/reindex", summary="Tạo job reindex (embedding/chunking) — toàn bộ hoặc theo doc_type/file")
def reindex(body: ReindexBody, request: Request, principal: Principal = Depends(require_role("admin")),
            db: Session = Depends(get_db)) -> dict:
    payload = body.model_dump(exclude_none=True)
    if payload.get("rechunk") in ("true", "false"):
        payload["rechunk"] = payload["rechunk"] == "true"
    job = enqueue_job(db, "reindex", payload, document_id=body.document_id, created_by=principal.subject)
    audit_action(request, principal, "reindex", job.id, **payload)
    return job_view(job)


# ------------------------------------------------------------------ conflicts
@router.get("/conflicts", summary="Danh sách mâu thuẫn (mặc định chưa xử lý)")
def list_conflicts(status: str = "open", limit: int = Query(100, le=500), principal: Principal = Depends(require_role("supervisor")),
                   db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Conflict).where(Conflict.status == status).order_by(Conflict.created_at.desc()).limit(limit)).scalars()
    return [{"id": c.id, "kind": c.kind, "entity_key": c.entity_key, "record_ids": c.record_ids, "details": c.details,
             "status": c.status, "resolution": c.resolution, "resolved_by": c.resolved_by, "created_at": c.created_at,
             "resolved_at": c.resolved_at} for c in rows]


class ConflictResolveBody(BaseModel):
    action: Literal["keep_one", "dismiss", "resolved"]
    keep_record_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/conflicts/{conflict_id}/resolve", summary="Xử lý mâu thuẫn: giữ 1 nguồn / bỏ qua / đã xử lý")
def resolve_conflict(conflict_id: str, body: ConflictResolveBody, request: Request,
                     principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    c = db.get(Conflict, conflict_id)
    if c is None:
        raise NotFoundError("Không có conflict")
    kb_service.resolve_conflict(db, c, action=body.action, actor=principal.subject, note=body.note, keep_record_id=body.keep_record_id)
    audit_action(request, principal, "conflict_resolve", conflict_id, action=body.action, keep=body.keep_record_id)
    return {"id": c.id, "status": c.status, "resolution": c.resolution}
