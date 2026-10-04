"""KB lifecycle (Phần C):

C2 Update & replace — tài liệu mới cùng series (series_key + doc_type + store_code) thay tài liệu cũ:
    bản cũ KHÔNG bị xoá: document + record chuyển is_active=False, status=superseded, valid_to = ngày
    hiệu lực của bản mới -> query mặc định (is_active=True) chỉ thấy bản mới; câu hỏi "tại thời điểm"
    (as_of) vẫn trả lời được từ lịch sử. Nạp BÙ bản cũ hơn bản đang hiệu lực -> vào thẳng lịch sử.
    replace_mode: document (mặc định) | record (file cập nhật một phần: chỉ thay record cùng
    entity_key, vd giá của đúng các SKU có trong file) | none (không bao giờ thay).
C3 Dedup — cùng nội dung (content_hash chuẩn hoá) ở 2 document khác nhau: giữ bản effective_date mới
    hơn, bản kia status=duplicate, is_active=False, duplicate_of trỏ bản giữ lại.
C4 Soft delete / restore cho record và cho cả file. Xoá file đang thay thế 1 bản cũ -> bản cũ được
    khôi phục hiệu lực (rollback dữ liệu tự nhiên).
Mọi hàm chạy trong transaction của caller, bump kb_state.version, đồng bộ trạng thái xuống kb_chunks.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.errors import ConflictError, NotFoundError, ValidationFailed
from core.logging import log_event
from core.models import Document, KBRecordRow, utcnow
from graph.resolve import fold_text
from kb.repository import bump_kb_version, set_chunks_state_for_records

logger = logging.getLogger(__name__)

DEDUP_MIN_CHARS = 40

# token ngày/tháng/phiên bản trong tên file — bỏ đi để 2 kỳ của cùng 1 loại tài liệu có cùng series
_VERSION_TOKEN_RES = (
    re.compile(r"(?<![a-z])(?:thang|t)[ _-]?\d{1,2}(?:[ _-]\d{2,4})?(?![a-z0-9])"),  # thang_8, t8, thang 8 2026
    re.compile(r"(?<![a-z0-9])(?:q|quy)[ _-]?[1-4](?:[ _-]\d{2,4})?(?![a-z0-9])"),  # q3, quy 3 2026
    re.compile(r"(?<![0-9])\d{4}[ _.-]\d{1,2}(?:[ _.-]\d{1,2})?(?![0-9])"),          # 2026-08, 2026_08_01
    re.compile(r"(?<![0-9])\d{1,2}[ _.-]\d{1,2}[ _.-]\d{4}(?![0-9])"),               # 01-08-2026
    re.compile(r"(?<![0-9])(?:19|20)\d{2}(?![0-9])"),                                 # 2026
    re.compile(r"(?<![a-z])v(?:er(?:sion)?)?[ _.-]?\d+(?:[._ ]\d+)*(?![a-z0-9])"),    # v3.2, ver2
    re.compile(r"(?<![a-z])(?:final|moi|cu|update|cap nhat|ban sua|draft|copy)(?![a-z])"),
)


def derive_series_key(filename: str) -> str:
    """"01_bang_gia_thang_8_2026.xlsx" -> "bang_gia"; "Chính sách đổi trả v3.2.docx" ->
    "chinh_sach_doi_tra". Bỏ số thứ tự đầu tên, token ngày/tháng/quý/phiên bản. Hai file cùng
    series_key (+ cùng doc_type, store_code) => file có effective_date mới hơn thay file cũ (C2)."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    s = fold_text(stem.replace("_", " ").replace("-", " "))
    s = re.sub(r"^\d{1,3}\s+", "", s)
    for rx in _VERSION_TOKEN_RES:
        s = rx.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "tai_lieu"


def _eff(d: date | None) -> date:
    return d or date.min


def _sync(session: Session, records: list[KBRecordRow]) -> None:
    """Chép trạng thái record xuống chunk (theo nhóm giá trị để ít câu UPDATE)."""
    groups: dict[tuple, list[str]] = {}
    for r in records:
        groups.setdefault((r.is_active, r.valid_to, r.deleted_at is not None), []).append(r.record_id)
    for (active, valid_to, deleted), ids in groups.items():
        set_chunks_state_for_records(session, ids, is_active=active, valid_to=valid_to, deleted=deleted)


def _doc_records(session: Session, doc_id: str) -> list[KBRecordRow]:
    return list(session.execute(select(KBRecordRow).where(KBRecordRow.document_id == doc_id)).scalars())


def _active_series(session: Session, doc: Document) -> list[Document]:
    q = select(Document).where(Document.workspace_id == doc.workspace_id, Document.series_key == doc.series_key,
                               Document.doc_type == doc.doc_type, Document.id != doc.id, Document.deleted_at.is_(None),
                               Document.is_active.is_(True), Document.replace_mode != "none")
    q = q.where(Document.store_code.is_(None)) if doc.store_code is None else q.where(Document.store_code == doc.store_code)
    return list(session.execute(q).scalars())


def supersede(session: Session, old: Document, new: Document) -> int:
    valid_to = new.effective_date or date.today()
    old.is_active, old.status, old.superseded_by_id, old.valid_to = False, "superseded", new.id, valid_to
    new.supersedes_id = old.id
    recs = [r for r in _doc_records(session, old.id) if r.deleted_at is None]
    for r in recs:
        if r.status == "active":
            r.status = "superseded"
        r.is_active = False
        r.valid_to = r.valid_to or valid_to
    _sync(session, recs)
    log_event(logger, "document_superseded", old=old.id, new=new.id, valid_to=str(valid_to), records=len(recs))
    return len(recs)


def _set_records_active(session: Session, doc: Document, active: bool, valid_to: date | None = None) -> list[KBRecordRow]:
    recs = [r for r in _doc_records(session, doc.id) if r.deleted_at is None]
    for r in recs:
        if r.status in ("duplicate", "quarantined") and active:
            continue
        r.is_active = active
        r.status = "active" if active else ("superseded" if r.status == "active" else r.status)
        r.valid_to = None if active else (valid_to or r.valid_to)
    _sync(session, recs)
    return recs


def activate_document(session: Session, doc: Document) -> dict[str, Any]:
    """Đưa document (đã có record) vào trạng thái hiệu lực, áp dụng replace (C2) + dedup (C3)."""
    summary: dict[str, Any] = {"superseded_documents": [], "superseded_records": 0, "historical": False, "duplicates": 0}
    others = _active_series(session, doc) if doc.replace_mode == "document" else []
    newer_than_new = [o for o in others if _eff(o.effective_date) > _eff(doc.effective_date)]
    if newer_than_new:
        # nạp bù bản cũ hơn bản đang hiệu lực -> vào lịch sử, không đè bản mới
        current = max(newer_than_new, key=lambda o: _eff(o.effective_date))
        doc.is_active, doc.status, doc.superseded_by_id, doc.valid_to = False, "superseded", current.id, current.effective_date
        _set_records_active(session, doc, False, current.effective_date)
        summary["historical"] = True
        log_event(logger, "document_historical", document_id=doc.id, current=current.id)
    else:
        for old in others:
            summary["superseded_records"] += supersede(session, old, doc)
            summary["superseded_documents"].append(old.id)
        doc.is_active, doc.status, doc.valid_to = True, "active", None
        recs = _set_records_active(session, doc, True)
        if doc.replace_mode == "record":
            summary["superseded_records"] += _replace_by_entity(session, doc, recs)
    summary["duplicates"] = dedup_document(session, doc)
    bump_kb_version(session)
    return summary


def _replace_by_entity(session: Session, doc: Document, recs: list[KBRecordRow]) -> int:
    keys = {r.entity_key for r in recs if r.entity_key and r.is_active}
    if not keys:
        return 0
    olds = list(session.execute(select(KBRecordRow).where(
        KBRecordRow.workspace_id == doc.workspace_id,
        KBRecordRow.entity_key.in_(keys), KBRecordRow.document_id != doc.id, KBRecordRow.is_active.is_(True),
        KBRecordRow.deleted_at.is_(None))).scalars())
    valid_to = doc.effective_date or date.today()
    changed = []
    for o in olds:
        if _eff(o.effective_date) <= _eff(doc.effective_date):
            o.is_active, o.status, o.valid_to = False, "superseded", valid_to
            changed.append(o)
    _sync(session, changed)
    return len(changed)


def dedup_document(session: Session, doc: Document) -> int:
    """C3: record của doc trùng nội dung với record ĐANG HIỆU LỰC ở document khác -> giữ bản mới hơn."""
    recs = [r for r in _doc_records(session, doc.id) if r.deleted_at is None and r.is_active and len(r.content) >= DEDUP_MIN_CHARS]
    if not recs:
        return 0
    by_hash = {r.content_hash: r for r in recs}
    others = list(session.execute(select(KBRecordRow).where(
        KBRecordRow.workspace_id == doc.workspace_id,
        KBRecordRow.content_hash.in_(list(by_hash)), KBRecordRow.document_id != doc.id,
        KBRecordRow.is_active.is_(True), KBRecordRow.deleted_at.is_(None))).scalars())
    changed: list[KBRecordRow] = []
    for other in others:
        mine = by_hash.get(other.content_hash)
        if mine is None:
            continue
        keep, drop = (mine, other) if _eff(mine.effective_date) >= _eff(other.effective_date) else (other, mine)
        drop.status, drop.is_active, drop.duplicate_of = "duplicate", False, keep.record_id
        changed.append(drop)
    _sync(session, changed)
    if changed:
        log_event(logger, "records_deduplicated", document_id=doc.id, count=len(changed))
    return len(changed)


# =====================================================================================
# C4 — soft delete / restore
# =====================================================================================

def soft_delete_document(session: Session, doc: Document, actor: str) -> dict[str, Any]:
    if doc.deleted_at is not None:
        return {"document_id": doc.id, "already_deleted": True, "reactivated": None}
    now = utcnow()
    was_active = doc.is_active
    doc.deleted_at, doc.status, doc.is_active = now, "deleted", False
    doc.meta = {**(doc.meta or {}), "deleted_by": actor}
    recs = _doc_records(session, doc.id)
    for r in recs:
        if r.deleted_at is None:
            r.deleted_at, r.is_active = now, False
            r.meta = {**(r.meta or {}), "status_before_delete": r.status}
            r.status = "deleted"
    _sync(session, recs)
    reactivated = None
    if was_active and doc.supersedes_id:
        prev = session.get(Document, doc.supersedes_id)
        if prev is not None and prev.deleted_at is None and prev.superseded_by_id == doc.id:
            prev.superseded_by_id = None
            activate_document(session, prev)
            reactivated = prev.id
    from analytics.tables import set_tables_active

    set_tables_active(session, doc.id, False)
    bump_kb_version(session)
    log_event(logger, "document_soft_deleted", document_id=doc.id, actor=actor, records=len(recs), reactivated=reactivated)
    return {"document_id": doc.id, "records": len(recs), "reactivated": reactivated}


def restore_document(session: Session, doc: Document, actor: str) -> dict[str, Any]:
    if doc.deleted_at is None:
        raise ConflictError("Document chưa bị xoá, không cần khôi phục.")
    deleted_at = doc.deleted_at
    doc.deleted_at, doc.status = None, "pending"
    recs = _doc_records(session, doc.id)
    for r in recs:
        if r.deleted_at == deleted_at:  # chỉ khôi phục record bị xoá CÙNG lúc với file
            r.deleted_at = None
            r.status = (r.meta or {}).get("status_before_delete", "active")
            if r.status == "deleted":
                r.status = "active"
    _sync(session, recs)
    summary = activate_document(session, doc)
    from analytics.tables import set_tables_active

    set_tables_active(session, doc.id, True)
    log_event(logger, "document_restored", document_id=doc.id, actor=actor)
    return {"document_id": doc.id, **summary}


def soft_delete_record(session: Session, rec: KBRecordRow, actor: str) -> KBRecordRow:
    if rec.deleted_at is not None:
        return rec
    rec.meta = {**(rec.meta or {}), "status_before_delete": rec.status, "deleted_by": actor}
    rec.deleted_at, rec.is_active, rec.status = utcnow(), False, "deleted"
    _sync(session, [rec])
    bump_kb_version(session)
    return rec


def restore_record(session: Session, rec: KBRecordRow, actor: str) -> KBRecordRow:
    if rec.deleted_at is None:
        raise ConflictError("Record chưa bị xoá.")
    doc = session.get(Document, rec.document_id)
    if doc is None or doc.deleted_at is not None:
        raise ConflictError("File chứa record đang bị xoá — khôi phục file trước.")
    prev = (rec.meta or {}).get("status_before_delete", "active")
    rec.deleted_at = None
    rec.status = prev if prev != "deleted" else "active"
    rec.is_active = doc.is_active and rec.status == "active"
    if not doc.is_active and rec.status == "active":
        rec.status = "superseded"
    _sync(session, [rec])
    bump_kb_version(session)
    return rec


def get_document_or_404(session: Session, doc_id: str) -> Document:
    doc = session.get(Document, doc_id)
    if doc is None:
        raise NotFoundError(f"Không có document {doc_id}")
    return doc


def get_record_or_404(session: Session, record_id: str) -> KBRecordRow:
    rec = session.get(KBRecordRow, record_id)
    if rec is None:
        raise NotFoundError(f"Không có record {record_id}")
    return rec


def validate_replace_mode(mode: str) -> str:
    if mode not in ("document", "record", "none"):
        raise ValidationFailed("replace_mode phải là document | record | none")
    return mode
