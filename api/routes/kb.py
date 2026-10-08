"""KB theo workspace: health, documents (chi tiết/xoá/khôi phục/duyệt), records (lọc/sửa/xoá/khôi phục),
chunks (nguồn citation), reindex, conflicts."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db, get_services
from core.errors import ForbiddenError, NotFoundError
from core.i18n import L
from core.models import ChunkRow, Conflict, DataTable, Document, Entity, EntityMention, Job, KBRecordRow
from core.principal import WorkspaceContext
from domains import get_pack
from graph.store import GraphUnavailable
from kb import service as kb_service
from kb.chunking import embed_chunks
from kb.health import kb_health
from kb.lifecycle import restore_document, restore_record, soft_delete_document, soft_delete_record
from qa.citation import format_citation
from worker.queue import enqueue_job, job_view

router = APIRouter(prefix="/w/{workspace_id}", tags=["kb"])


def _scope_ok(ctx: WorkspaceContext, store_code: str | None) -> bool:
    mine = ctx.scope_value
    return mine is None or store_code is None or store_code == mine


def _record_view(r: KBRecordRow, doc: Document | None = None) -> dict:
    return {"record_id": r.record_id, "document_id": r.document_id, "source_file": r.source_file, "source_type": r.source_type,
            "doc_type": r.doc_type, "location": r.location, "content": r.content, "scope": r.store_code, "skus": r.skus,
            "category": r.category, "effective_date": r.effective_date, "valid_to": r.valid_to, "version": r.version,
            "status": r.status, "is_active": r.is_active, "duplicate_of": r.duplicate_of, "entity_key": r.entity_key,
            "confidence": r.confidence, "suspicious_content": r.suspicious_content, "metadata": r.meta,
            "manually_edited": r.manually_edited, "edited_by": r.edited_by, "edited_at": r.edited_at,
            "deleted_at": r.deleted_at, "updated_at": r.updated_at,
            "citation": format_citation(doc.title, doc.source_type, r.location) if doc else None}


def doc_view(d: Document) -> dict:
    return {"document_id": d.id, "filename": d.filename, "title": d.title, "series_key": d.series_key, "source_type": d.source_type,
            "doc_type": d.doc_type, "scope": d.store_code, "effective_date": d.effective_date, "valid_to": d.valid_to,
            "version": d.version, "status": d.status, "is_active": d.is_active, "replace_mode": d.replace_mode,
            "supersedes_id": d.supersedes_id, "superseded_by_id": d.superseded_by_id, "record_count": d.record_count,
            "table_count": d.table_count, "size_bytes": d.size_bytes, "uploaded_by": d.uploaded_by, "summary": d.summary,
            "semantic_profile": d.semantic_profile or {}, "created_at": d.created_at, "updated_at": d.updated_at,
            "deleted_at": d.deleted_at, "metadata": d.meta}


def get_document(db: Session, ctx: WorkspaceContext, document_id: str) -> Document:
    d = db.get(Document, document_id)
    if d is None or d.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có tài liệu", "Document not found"))
    if not _scope_ok(ctx, d.store_code):
        raise ForbiddenError(L("Tài liệu thuộc phạm vi khác", "This document belongs to a different scope"))
    return d


def get_record(db: Session, ctx: WorkspaceContext, record_id: str) -> KBRecordRow:
    r = db.get(KBRecordRow, record_id)
    if r is None or r.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có record", "Passage not found"))
    if not _scope_ok(ctx, r.store_code):
        raise ForbiddenError(L("Record thuộc phạm vi khác", "This passage belongs to a different scope"))
    return r


# ------------------------------------------------------------------ health / overview
@router.get("/kb/health", summary="Sức khoẻ KB của workspace")
def health(days: int = Query(7, ge=1, le=90), ctx: WorkspaceContext = Depends(workspace("viewer")),
           db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    graph_stats: Any = {"enabled": False}
    if get_pack(ctx.domain_pack).rule_graph:
        try:
            store = services.graph_store(ctx.workspace_id)
            graph_stats = store.stats() if store else {"enabled": False}
        except GraphUnavailable as e:
            graph_stats = {"error": str(e)[:300]}
    return kb_health(db, services.cfg, workspace_id=ctx.workspace_id, days=days, graph_stats=graph_stats,
                     embedding_model=services.embedder.model_name)


# ------------------------------------------------------------------ documents
@router.get("/documents", summary="Danh sách tài liệu")
def list_documents(status: str | None = None, doc_type: str | None = None, q: str | None = None, include_deleted: bool = False,
                   page: int = Query(1, ge=1), page_size: int = Query(50, le=200),
                   ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    query = select(Document).where(Document.workspace_id == ctx.workspace_id)
    if not include_deleted:
        query = query.where(Document.deleted_at.is_(None))
    if ctx.scope_value:
        query = query.where(or_(Document.store_code == ctx.scope_value, Document.store_code.is_(None)))
    if status:
        query = query.where(Document.status == status)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if q:
        query = query.where(or_(Document.filename.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%"), Document.summary.ilike(f"%{q}%")))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars()
    return {"total": total, "page": page, "page_size": page_size, "items": [doc_view(d) for d in rows]}


@router.get("/documents/{document_id}", summary="Chi tiết tài liệu: hồ sơ ngữ nghĩa, bảng, thực thể, job")
def document_detail(document_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    d = get_document(db, ctx, document_id)
    jobs = db.execute(select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc()).limit(20)).scalars()
    tables = db.execute(select(DataTable).where(DataTable.document_id == document_id, DataTable.is_active.is_(True))).scalars()
    ents = db.execute(select(Entity, func.count(EntityMention.id)).join(EntityMention, EntityMention.entity_id == Entity.id)
                      .where(EntityMention.document_id == document_id).group_by(Entity.id)
                      .order_by(func.count(EntityMention.id).desc()).limit(50)).all()
    rec_count = db.execute(select(func.count()).select_from(KBRecordRow).where(KBRecordRow.document_id == document_id,
                                                                               KBRecordRow.deleted_at.is_(None))).scalar_one()
    return {**doc_view(d), "jobs": [job_view(j) for j in jobs], "record_count_live": rec_count,
            "tables": [{"id": t.id, "name": t.name, "title": t.title, "row_count": t.row_count, "columns": t.columns,
                        "description": t.description, "source_location": t.source_location} for t in tables],
            "entities": [{"id": e.id, "type": e.type, "name": e.name, "mentions": n, "description": e.description} for e, n in ents]}


@router.get("/documents/{document_id}/records", summary="Record của 1 tài liệu (đọc nội dung đã trích)")
def document_records(document_id: str, page: int = Query(1, ge=1), page_size: int = Query(100, le=500),
                     ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    d = get_document(db, ctx, document_id)
    q = select(KBRecordRow).where(KBRecordRow.document_id == document_id, KBRecordRow.deleted_at.is_(None))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(KBRecordRow.record_id).offset((page - 1) * page_size).limit(page_size)).scalars()
    return {"total": total, "items": [_record_view(r, d) for r in rows]}


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    doc_type: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,31}$")
    effective_date: date | None = None
    version: str | None = None
    scope: str | None = None


@router.patch("/documents/{document_id}", summary="Sửa metadata tài liệu (tên hiển thị, loại, ngày hiệu lực)")
def patch_document(document_id: str, body: DocumentPatch, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                   db: Session = Depends(get_db)) -> dict:
    d = get_document(db, ctx, document_id)
    ch = body.model_dump(exclude_unset=True)
    for k, v in ch.items():
        setattr(d, "store_code" if k == "scope" else k, v)
    if ch:
        enqueue_job(db, "knowledge_extract", {"document_id": d.id, "only": ["chunks_meta"]}, document_id=d.id,
                    created_by=ctx.subject, workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "document_update", document_id, fields=sorted(ch))
    return doc_view(d)


@router.delete("/documents/{document_id}", summary="Xoá mềm tài liệu (bản cũ bị thay thế sẽ có hiệu lực lại)")
def delete_document(document_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    out = soft_delete_document(db, get_document(db, ctx, document_id), ctx.subject)
    ids = [document_id] + ([out["reactivated"]] if out.get("reactivated") else [])
    enqueue_job(db, "document_lifecycle", {"document_ids": ids}, document_id=document_id, created_by=ctx.subject, workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "document_delete", document_id, reactivated=out.get("reactivated"))
    return out


@router.post("/documents/{document_id}/restore")
def restore_document_ep(document_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    d = db.get(Document, document_id)
    if d is None or d.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có tài liệu", "Document not found"))
    out = restore_document(db, d, ctx.subject)
    enqueue_job(db, "document_lifecycle", {"document_ids": [document_id, *out.get("superseded_documents", [])]},
                document_id=document_id, created_by=ctx.subject, workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "document_restore", document_id)
    return out


@router.post("/documents/{document_id}/approve", summary="Duyệt tài liệu bị chặn (nghi prompt injection) sau khi kiểm tra")
def approve_document(document_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    from ingest.pipeline import approve_blocked_document

    d = get_document(db, ctx, document_id)
    if d.status != "blocked":
        raise ForbiddenError(L(f"Chỉ duyệt được tài liệu đang blocked (hiện: {d.status})", f"Only blocked documents can be approved (current: {d.status})"))
    out = approve_blocked_document(db, d, ctx.subject)
    audit_action(request, ctx.principal, "document_approve", document_id, records=out.get("approved_records"))
    return out


@router.post("/documents/{document_id}/reprocess", summary="Trích lại tri thức (tóm tắt, entity, bảng) cho tài liệu")
def reprocess_document(document_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    d = get_document(db, ctx, document_id)
    job = enqueue_job(db, "knowledge_extract", {"document_id": d.id, "force": True}, document_id=d.id, created_by=ctx.subject,
                      workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "document_reprocess", document_id)
    return job_view(job)


# ------------------------------------------------------------------ records
@router.get("/records", summary="Duyệt record (phân trang, lọc doc_type/scope/file/status/sku/q)")
def list_records(doc_type: str | None = None, scope: str | None = None, source_file: str | None = None, document_id: str | None = None,
                 status: str | None = None, sku: str | None = None, q: str | None = Query(None, max_length=200), include_deleted: bool = False,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    query = select(KBRecordRow).where(KBRecordRow.workspace_id == ctx.workspace_id)
    if not include_deleted:
        query = query.where(KBRecordRow.deleted_at.is_(None))
    if ctx.scope_value:
        query = query.where(or_(KBRecordRow.store_code == ctx.scope_value, KBRecordRow.store_code.is_(None)))
    if doc_type:
        query = query.where(KBRecordRow.doc_type == doc_type)
    if scope:
        query = query.where(KBRecordRow.store_code == scope.upper())
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
    rows = db.execute(query.order_by(KBRecordRow.updated_at.desc(), KBRecordRow.record_id).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    docs = {d.id: d for d in db.execute(select(Document).where(Document.id.in_({r.document_id for r in rows}))).scalars()}
    return {"total": total, "page": page, "page_size": page_size, "items": [_record_view(r, docs.get(r.document_id)) for r in rows]}


@router.get("/records/{record_id:path}/detail")
def record_detail(record_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    r = get_record(db, ctx, record_id)
    chunks = db.execute(select(ChunkRow).where(ChunkRow.record_id == record_id).order_by(ChunkRow.chunk_id)).scalars().all()
    return {**_record_view(r, db.get(Document, r.document_id)),
            "chunks": [{"chunk_id": c.chunk_id, "role": c.role, "text": c.text, "deleted": c.deleted} for c in chunks]}


class RecordPatch(BaseModel):
    content: str | None = Field(default=None, max_length=50000)
    scope: str | None = None
    skus: list[str] | None = None
    category: str | None = None
    effective_date: date | None = None
    version: str | None = None
    doc_type: str | None = None
    metadata: dict | None = None
    is_active: bool | None = None


@router.patch("/records/{record_id:path}", summary="Sửa tay record — chunk/embedding/graph tự cập nhật")
def patch_record(record_id: str, body: RecordPatch, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                 db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    rec = get_record(db, ctx, record_id)
    changes = body.model_dump(exclude_unset=True)
    if "scope" in changes:
        changes["store_code"] = changes.pop("scope")
    out = kb_service.edit_record(db, services.cfg, rec, changes, ctx.subject)
    db.commit()
    emb = embed_chunks(services.sm, services.cfg, services.embedder, chunk_ids=None, document_ids=[rec.document_id])
    audit_action(request, ctx.principal, "record_edit", record_id, fields=sorted(changes), before=out["before"])
    return {**out, "embedding": emb, "record": _record_view(get_record(db, ctx, record_id))}


@router.delete("/records/{record_id:path}")
def delete_record(record_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    rec = soft_delete_record(db, get_record(db, ctx, record_id), ctx.subject)
    enqueue_job(db, "graph_sync", {"document_id": rec.document_id, "llm": False}, document_id=rec.document_id, created_by=ctx.subject,
                workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "record_delete", record_id)
    return _record_view(rec)


@router.post("/records/{record_id:path}/restore")
def restore_record_ep(record_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    rec = restore_record(db, get_record(db, ctx, record_id), ctx.subject)
    enqueue_job(db, "graph_sync", {"document_id": rec.document_id, "llm": False}, document_id=rec.document_id, created_by=ctx.subject,
                workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "record_restore", record_id)
    return _record_view(rec)


# ------------------------------------------------------------------ chunks (nguồn citation)
@router.get("/chunks/{chunk_id:path}", summary="Đoạn gốc của 1 citation (+ parent, nguồn, vị trí)")
def get_chunk(chunk_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    c = db.get(ChunkRow, chunk_id)
    if c is None or c.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không tìm thấy đoạn nguồn (có thể đã được reindex lại)", "Source passage not found (it may have been reindexed)"))
    if not _scope_ok(ctx, c.store_code):
        raise ForbiddenError(L("Đoạn nguồn thuộc phạm vi khác", "This source passage belongs to a different scope"))
    doc = db.get(Document, c.document_id)
    rec = db.get(KBRecordRow, c.record_id)
    parent = db.get(ChunkRow, c.parent_chunk_id) if c.parent_chunk_id else None
    return {"chunk_id": c.chunk_id, "text": c.text, "parent_text": parent.text if parent else None,
            "record_content": rec.content if rec else None, "record_id": c.record_id, "location": c.location, "role": c.role,
            "citation": format_citation(doc.title, doc.source_type, c.location) if doc else None,
            "document": {"document_id": doc.id, "title": doc.title, "filename": doc.filename, "doc_type": doc.doc_type,
                         "effective_date": doc.effective_date, "status": doc.status, "source_type": doc.source_type} if doc else None,
            "is_active": c.is_active, "effective_date": c.effective_date, "valid_to": c.valid_to, "scope": c.store_code}


# ------------------------------------------------------------------ reindex
class ReindexBody(BaseModel):
    doc_type: str | None = None
    document_id: str | None = None
    rechunk: Literal["auto", "true", "false"] = "auto"
    model: str | None = None
    dim: int | None = None
    force: bool = False


@router.post("/kb/reindex", summary="Job reindex (embedding/chunking) cho workspace")
def reindex(body: ReindexBody, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    payload = {**body.model_dump(exclude_none=True), "workspace_id": ctx.workspace_id}
    if payload.get("rechunk") in ("true", "false"):
        payload["rechunk"] = payload["rechunk"] == "true"
    job = enqueue_job(db, "reindex", payload, document_id=body.document_id, created_by=ctx.subject, workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "reindex", job.id, **{k: v for k, v in payload.items() if k != "workspace_id"})
    return job_view(job)


# ------------------------------------------------------------------ conflicts
@router.get("/conflicts", summary="Mâu thuẫn giữa các nguồn (mặc định chưa xử lý)")
def list_conflicts(status: str = "open", limit: int = Query(100, le=500), ctx: WorkspaceContext = Depends(workspace("viewer")),
                   db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Conflict).where(Conflict.workspace_id == ctx.workspace_id, Conflict.status == status)
                      .order_by(Conflict.created_at.desc()).limit(limit)).scalars()
    return [{"id": c.id, "kind": c.kind, "entity_key": c.entity_key, "record_ids": c.record_ids, "details": c.details,
             "status": c.status, "resolution": c.resolution, "resolved_by": c.resolved_by, "created_at": c.created_at,
             "resolved_at": c.resolved_at} for c in rows]


class ConflictResolveBody(BaseModel):
    action: Literal["keep_one", "dismiss", "resolved"]
    keep_record_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/conflicts/{conflict_id}/resolve", summary="Xử lý mâu thuẫn: giữ 1 nguồn / bỏ qua / đã xử lý")
def resolve_conflict(conflict_id: str, body: ConflictResolveBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                     db: Session = Depends(get_db)) -> dict:
    c = db.get(Conflict, conflict_id)
    if c is None or c.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có conflict", "Conflict not found"))
    kb_service.resolve_conflict(db, c, action=body.action, actor=ctx.subject, note=body.note, keep_record_id=body.keep_record_id)
    audit_action(request, ctx.principal, "conflict_resolve", conflict_id, action=body.action, keep=body.keep_record_id)
    return {"id": c.id, "status": c.status, "resolution": c.resolution}
