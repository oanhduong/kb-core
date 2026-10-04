"""Truy vấn/ghi KB dùng chung cho QA, ingest, admin. Mọi thay đổi dữ liệu KB phải gọi bump_kb_version()
trong CÙNG transaction để BM25 cache/entity resolver các tiến trình khác biết mà làm mới."""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from core.models import ChunkRow, Document, KBRecordRow, KBState, utcnow

_XLSX_LOC_RE = re.compile(r"^(?P<sheet>.+?)!A(?P<row>\d+)(?::[A-Z]+(?P<row_end>\d+))?$")


def bump_kb_version(session: Session) -> int:
    row = session.get(KBState, 1)
    if row is None:
        row = KBState(id=1, version=0)
        session.add(row)
    row.version = (row.version or 0) + 1
    row.updated_at = utcnow()
    session.flush()
    return row.version


def current_kb_version(session: Session) -> int:
    return int(session.execute(select(KBState.version).where(KBState.id == 1)).scalar() or 0)


def get_documents(session: Session, ids: Iterable[str]) -> dict[str, Document]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    return {d.id: d for d in session.execute(select(Document).where(Document.id.in_(ids))).scalars()}


def get_records(session: Session, ids: Iterable[str]) -> dict[str, KBRecordRow]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    return {r.record_id: r for r in session.execute(select(KBRecordRow).where(KBRecordRow.record_id.in_(ids))).scalars()}


def get_chunks(session: Session, ids: Iterable[str]) -> dict[str, ChunkRow]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    return {c.chunk_id: c for c in session.execute(select(ChunkRow).where(ChunkRow.chunk_id.in_(ids))).scalars()}


def context_chunks_for_child_ids(session: Session, child_ids: Sequence[str]) -> list[ChunkRow]:
    """child/single chunk id (kết quả retrieval) -> chunk đưa vào context: PARENT của child (đủ ngữ
    cảnh), hoặc chính nó nếu là single. Giữ thứ tự, khử trùng lặp."""
    children = get_chunks(session, child_ids)
    parent_ids = [c.parent_chunk_id for c in children.values() if c.role == "child" and c.parent_chunk_id]
    parents = get_chunks(session, parent_ids)
    out: list[ChunkRow] = []
    seen: set[str] = set()
    for cid in child_ids:
        c = children.get(cid)
        if c is None:
            continue
        target = parents.get(c.parent_chunk_id) if c.role == "child" and c.parent_chunk_id else c
        target = target or c
        if target.chunk_id not in seen and not target.deleted:
            seen.add(target.chunk_id)
            out.append(target)
    return out


def resolve_record_refs(session: Session, refs: Sequence[str]) -> list[str]:
    """record_id từ graph (kb/ids.record_id_for) -> record_id có thật trong KB. Khớp chính xác trước;
    không có (vd document ingest bằng agent đặt location dạng "Sheet!A5:D5") thì khớp theo document +
    sheet + dòng nằm trong khoảng. Giữ thứ tự refs."""
    exact = get_records(session, refs)
    out: list[str] = []
    missing = [r for r in refs if r not in exact]
    by_doc: dict[str, list[KBRecordRow]] = {}
    if missing:
        doc_ids = {r.split(":", 1)[0] for r in missing}
        for row in session.execute(select(KBRecordRow).where(KBRecordRow.document_id.in_(doc_ids))).scalars():
            by_doc.setdefault(row.document_id, []).append(row)
    for ref in refs:
        if ref in exact:
            out.append(ref)
            continue
        doc_id, _, loc = ref.partition(":")
        m = _XLSX_LOC_RE.match(loc)
        for row in by_doc.get(doc_id, []):
            if row.location == loc:
                out.append(row.record_id)
                break
            rm = _XLSX_LOC_RE.match(row.location)
            if m and rm and rm.group("sheet") == m.group("sheet"):
                lo, hi = int(rm.group("row")), int(rm.group("row_end") or rm.group("row"))
                if lo <= int(m.group("row")) <= hi:
                    out.append(row.record_id)
                    break
    return list(dict.fromkeys(out))


def context_chunks_for_records(session: Session, record_ids: Sequence[str], *, include_inactive: bool = False) -> list[ChunkRow]:
    """record_id -> chunk parent/single của record (chunk đưa vào context), giữ thứ tự record."""
    if not record_ids:
        return []
    q = select(ChunkRow).where(ChunkRow.record_id.in_(list(record_ids)), ChunkRow.role.in_(("parent", "single")),
                               ChunkRow.deleted.is_(False))
    if not include_inactive:
        q = q.where(ChunkRow.is_active.is_(True))
    rows = session.execute(q.order_by(ChunkRow.chunk_id)).scalars().all()
    order = {rid: i for i, rid in enumerate(record_ids)}
    return sorted(rows, key=lambda c: (order.get(c.record_id, 10**9), c.chunk_id))


def active_price_records_for_skus(session: Session, skus: Sequence[str], store_codes: Sequence[str | None] | None,
                                  doc_types: Sequence[str] = ("price_list", "promotion", "catalog")) -> list[KBRecordRow]:
    """Mọi record ĐANG hiệu lực nhắc tới các SKU (để phát hiện mâu thuẫn giá — B4)."""
    if not skus:
        return []
    q = select(KBRecordRow).where(KBRecordRow.skus.op("&&")(list(skus)), KBRecordRow.is_active.is_(True),
                                  KBRecordRow.deleted_at.is_(None), KBRecordRow.doc_type.in_(list(doc_types)))
    if store_codes is not None:
        concrete = [s for s in store_codes if s]
        cond = KBRecordRow.store_code.in_(concrete) if concrete else None
        if None in store_codes:
            cond = KBRecordRow.store_code.is_(None) if cond is None else (cond | KBRecordRow.store_code.is_(None))
        if cond is not None:
            q = q.where(cond)
    return list(session.execute(q.limit(200)).scalars())


def record_counts_by_doc_type(session: Session) -> dict[str, dict[str, int]]:
    rows = session.execute(
        select(KBRecordRow.doc_type, KBRecordRow.status, func.count()).where(KBRecordRow.deleted_at.is_(None))
        .group_by(KBRecordRow.doc_type, KBRecordRow.status)).all()
    out: dict[str, dict[str, int]] = {}
    for dt, status, n in rows:
        out.setdefault(dt, {})[status] = int(n)
    return out


def set_chunks_state_for_records(session: Session, record_ids: Sequence[str], **values: Any) -> int:
    """Đồng bộ cột trạng thái (is_active/valid_to/deleted) từ record xuống chunk."""
    if not record_ids:
        return 0
    res = session.execute(update(ChunkRow).where(ChunkRow.record_id.in_(list(record_ids))).values(**values))
    return res.rowcount or 0


def raw_sql(session: Session, sql: str, **params: Any) -> list[Any]:
    return list(session.execute(text(sql), params))
