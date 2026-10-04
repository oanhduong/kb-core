"""Thao tác admin trên KB (dùng bởi API D1 + Admin UI): sửa tay record (khi extraction sai), xoá
mềm/khôi phục record & file, xử lý conflict, duyệt file bị chặn. Mọi thao tác ghi audit log."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from core.config import AppConfig
from core.errors import ValidationFailed
from core.logging import log_event
from core.models import Conflict, Document, KBRecordRow, utcnow
from graph.resolve import normalize_store_code
from graph.sync import units_cache_path
from ingest.metadata import find_skus
from ingest.units import load_units_file, save_units
from kb.chunking import rebuild_chunks
from kb.ids import content_hash
from kb.repository import bump_kb_version
from worker.queue import enqueue_job

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = ("content", "store_code", "skus", "category", "effective_date", "version", "doc_type", "metadata", "is_active")


def edit_record(session: Session, cfg: AppConfig, rec: KBRecordRow, changes: dict[str, Any], actor: str,
                embed: Any = None) -> dict[str, Any]:
    """Sửa tay 1 record: cập nhật nội dung/metadata -> sinh lại chunk -> embed lại (qua callback) ->
    cập nhật cache unit (để graph build lại dùng dữ liệu đã sửa) -> đẩy graph_sync cho file."""
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationFailed(f"Không sửa được field: {sorted(unknown)}. Được sửa: {list(EDITABLE_FIELDS)}")
    before = {k: getattr(rec, "meta" if k == "metadata" else k) for k in changes}
    if "content" in changes:
        content = (changes["content"] or "").strip()
        if not content:
            raise ValidationFailed("content không được rỗng")
        rec.content = content
        rec.content_hash = content_hash(content)
        if "skus" not in changes:
            rec.skus = find_skus(content)
    if "skus" in changes:
        rec.skus = sorted({s.strip().upper() for s in changes["skus"] or [] if s.strip()})
    if "store_code" in changes:
        rec.store_code = normalize_store_code(changes["store_code"]) if changes["store_code"] else None
    if "category" in changes:
        rec.category = changes["category"]
    if "version" in changes:
        rec.version = changes["version"]
    if "doc_type" in changes:
        rec.doc_type = changes["doc_type"]
    if "effective_date" in changes:
        v = changes["effective_date"]
        rec.effective_date = v if isinstance(v, date) or v is None else date.fromisoformat(str(v))
    if "metadata" in changes:
        rec.meta = {**(rec.meta or {}), **(changes["metadata"] or {})}
    if "is_active" in changes:
        rec.is_active = bool(changes["is_active"])
        rec.status = "active" if rec.is_active else "superseded"
    rec.meta = {**(rec.meta or {}), "sku": rec.skus, "store_code": rec.store_code, "category": rec.category,
                "effective_date": rec.effective_date.isoformat() if rec.effective_date else None,
                "edit_history": [*(rec.meta or {}).get("edit_history", [])[-9:],
                                 {"by": actor, "at": utcnow().isoformat(), "fields": sorted(changes)}]}
    rec.manually_edited, rec.edited_by, rec.edited_at = True, actor, utcnow()
    doc = session.get(Document, rec.document_id)
    chunks = rebuild_chunks(session, cfg, doc, [rec])
    bump_kb_version(session)
    # cache unit cho graph: thay content của đúng vị trí
    units = load_units_file(units_cache_path(doc)) if doc else None
    if units:
        for u in units:
            if u.location == rec.location:
                u.content = rec.content
                headers = list((u.meta.get("fields") or {}).keys())
                if headers:
                    from ingest.units import _row_fields

                    u.meta["fields"] = _row_fields(rec.content, headers)
        save_units(units_cache_path(doc), units)
    enqueue_job(session, "graph_sync", {"document_id": rec.document_id, "llm": False}, document_id=rec.document_id,
                created_by=actor)
    session.flush()
    if embed is not None:
        embed([c.chunk_id for c in chunks])
    log_event(logger, "record_edited", record_id=rec.record_id, actor=actor, fields=sorted(changes))
    return {"record_id": rec.record_id, "chunks": len(chunks), "before": {k: str(v)[:500] for k, v in before.items()}}


def resolve_conflict(session: Session, conflict: Conflict, *, action: str, actor: str, note: str | None = None,
                     keep_record_id: str | None = None) -> Conflict:
    """action: keep_one (giữ 1 record, các record còn lại -> inactive) | dismiss (cả hai đều đúng, vd
    khác phạm vi) | resolved (đã sửa dữ liệu nguồn bằng cách khác)."""
    from kb.lifecycle import soft_delete_record

    if conflict.status != "open":
        raise ValidationFailed(f"Conflict đã ở trạng thái {conflict.status}")
    if action == "keep_one":
        if keep_record_id not in conflict.record_ids:
            raise ValidationFailed("keep_record_id phải là 1 trong các record của conflict")
        for rid in conflict.record_ids:
            if rid != keep_record_id:
                rec = session.get(KBRecordRow, rid)
                if rec is not None:
                    soft_delete_record(session, rec, actor)
        conflict.status = "resolved"
        conflict.resolution = f"Giữ {keep_record_id}, xoá mềm các record còn lại. {note or ''}".strip()
    elif action == "dismiss":
        conflict.status, conflict.resolution = "dismissed", note or "Không phải mâu thuẫn"
    elif action == "resolved":
        conflict.status, conflict.resolution = "resolved", note or "Đã xử lý ngoài hệ thống"
    else:
        raise ValidationFailed("action phải là keep_one | dismiss | resolved")
    conflict.resolved_by, conflict.resolved_at = actor, utcnow()
    session.flush()
    log_event(logger, "conflict_resolved", conflict_id=conflict.id, action=action, actor=actor)
    return conflict
