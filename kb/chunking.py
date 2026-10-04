"""Record -> chunk (+ embedding) dùng chung cho ingest, sửa tay record (PATCH) và reindex (C5).

- Chunk theo config (chunking.*) bằng ingest/chunkers.chunk_records — parent_child: parent ~1024 token
  đưa vào context, child ~256 token để embed/search; bảng/media có chiến lược riêng (role single).
- context_prefix xác định "<tiêu đề tài liệu> | <vị trí ngắn>" ghép trước text khi embed/BM25 —
  contextual retrieval rẻ, không tốn LLM (bản LLM tuỳ chọn: ingest.contextual_retrieval).
- Trạng thái lọc (doc_type, store, skus, effective_date, valid_to, is_active) chép từ record xuống
  chunk để pre-filter SQL không cần join.
- embed_chunks: idempotent theo (chunk_id, model, text_hash) — chạy lại chỉ embed phần còn thiếu/đổi.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import AppConfig
from core.models import ChunkRow, Document, KBRecordRow
from index.kb_store import KBVectorStore, ensure_model_index
from index.store import VectorRecord
from ingest.chunkers import ChunkerConfig, chunk_records
from ingest.metadata import short_location
from schemas import KBRecord

logger = logging.getLogger(__name__)


def chunker_config(cfg: AppConfig) -> ChunkerConfig:
    c = cfg.chunking
    return ChunkerConfig(strategy=c.strategy, fixed_chunk_tokens=c.fixed_chunk_tokens, fixed_overlap_tokens=c.fixed_overlap_tokens,
                         recursive_chunk_chars=c.recursive_chunk_chars, recursive_overlap_chars=c.recursive_overlap_chars,
                         small_table_max_rows=c.small_table_max_rows, media_max_gap_seconds=c.media_max_gap_seconds,
                         media_max_chunk_chars=c.media_max_chunk_chars, parent_chunk_tokens=c.parent_chunk_tokens,
                         child_chunk_tokens=c.child_chunk_tokens, child_overlap_tokens=c.child_overlap_tokens)


def _as_kbrecord(r: KBRecordRow) -> KBRecord:
    return KBRecord(record_id=r.record_id, source_file=r.source_file, source_type=r.source_type, doc_type=r.doc_type,
                    content=r.content, location=r.location, metadata=dict(r.meta or {}), confidence=r.confidence,
                    suspicious_content=list(r.suspicious_content or []))


def text_hash(text_: str) -> str:
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


def rebuild_chunks(session: Session, cfg: AppConfig, doc: Document, records: Sequence[KBRecordRow]) -> list[ChunkRow]:
    """Xoá chunk cũ của các record rồi sinh lại theo chunking config hiện tại. -> chunk mới."""
    if not records:
        return []
    by_id = {r.record_id: r for r in records}
    session.execute(delete(ChunkRow).where(ChunkRow.record_id.in_(list(by_id))))
    raw_chunks = chunk_records([_as_kbrecord(r) for r in records], chunker_config(cfg))
    fp = cfg.chunking.fingerprint()
    rows: list[ChunkRow] = []
    seen: set[str] = set()
    for ch in raw_chunks:
        owner_id = ch.metadata.get("source_record_id") or ch.parent_id
        owner = by_id.get(owner_id)
        if owner is None or ch.chunk_id in seen:
            continue
        seen.add(ch.chunk_id)
        role = ch.metadata.get("chunk_role") or "single"
        parent = ch.parent_id if role == "child" else None
        meta = {k: v for k, v in ch.metadata.items() if k not in ("fields",)}
        meta.update({"source_file": owner.source_file, "source_type": owner.source_type})
        rows.append(ChunkRow(
            chunk_id=ch.chunk_id, workspace_id=owner.workspace_id, record_id=owner.record_id,
            document_id=owner.document_id, parent_chunk_id=parent,
            role=role, text=ch.text, context_prefix=f"{doc.title} | {short_location(owner.source_type, ch.location)}",
            location=ch.location, meta=meta, doc_type=owner.doc_type, store_code=owner.store_code,
            skus=list(owner.skus or []), effective_date=owner.effective_date, valid_to=owner.valid_to,
            is_active=owner.is_active, deleted=owner.deleted_at is not None, chunking_fingerprint=fp,
        ))
    session.add_all(rows)
    session.flush()
    return rows


def embed_chunks(sm: sessionmaker, cfg: AppConfig, embedder: Any, chunk_ids: Sequence[str] | None = None, *,
                 document_ids: Sequence[str] | None = None, doc_types: Sequence[str] | None = None,
                 batch_size: int = 64, progress: Callable[[int, int], None] | None = None,
                 model: str | None = None, force: bool = False) -> dict[str, int]:
    """Embed chunk retrievable (child/single, chưa xoá) còn thiếu embedding cho model hiện tại hoặc text
    đã đổi (text_hash). Idempotent + resume tự nhiên: chạy lại chỉ làm phần còn thiếu."""
    model = model or embedder.model_name
    dim = embedder.dim
    store = KBVectorStore(sm, model, dim)
    with sm() as s, s.begin():
        ensure_model_index(s, model, dim)
    where = ["c.deleted = false", "c.role <> 'parent'"]
    params: dict[str, Any] = {"model": model}
    if chunk_ids is not None:
        where.append("c.chunk_id = ANY(:ids)")
        params["ids"] = list(chunk_ids)
    if document_ids:
        where.append("c.document_id = ANY(:docs)")
        params["docs"] = list(document_ids)
    if doc_types:
        where.append("c.doc_type = ANY(:dts)")
        params["dts"] = list(doc_types)
    sql = (f"SELECT c.chunk_id, c.text, c.context_prefix, e.text_hash FROM kb_chunks c "
           f"LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = :model "
           f"WHERE {' AND '.join(where)} ORDER BY c.chunk_id")
    with sm() as s:
        rows = s.execute(text(sql), params).all()
    todo = []
    for r in rows:
        body = f"{r.context_prefix}\n{r.text}" if r.context_prefix else r.text
        th = text_hash(f"{model}|{body}")
        if force or r.text_hash != th:
            todo.append((r.chunk_id, body, th))
    total, done = len(todo), 0
    skipped = len(rows) - total
    if progress:
        progress(0, total)
    for i in range(0, total, batch_size):
        batch = todo[i:i + batch_size]
        vecs = embedder.embed_passages([b[1] for b in batch])
        store.upsert([VectorRecord(id=cid, vector=v, text=body, metadata={"text_hash": th})
                      for (cid, body, th), v in zip(batch, vecs)])
        done += len(batch)
        if progress:
            progress(done, total)
    return {"embedded": done, "skipped_up_to_date": skipped, "model": model}


def records_of_document(session: Session, document_id: str, *, include_deleted: bool = False) -> list[KBRecordRow]:
    q = select(KBRecordRow).where(KBRecordRow.document_id == document_id)
    if not include_deleted:
        q = q.where(KBRecordRow.deleted_at.is_(None))
    return list(session.execute(q.order_by(KBRecordRow.record_id)).scalars())
