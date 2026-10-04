"""Vector store production trên schema KB (kb_chunks + chunk_embeddings), cùng interface
index.store.VectorStore để tái dùng HybridRetriever/BM25/RRF.

Khác index/store.PgVectorStore (bảng `chunks` 1 model cố định, dùng cho bench cũ):
- Nhiều embedding model cùng tồn tại: chunk_embeddings(chunk_id, model, embedding vector không cố
  định chiều). Mỗi model có HNSW riêng = partial expression index
      CREATE INDEX ... USING hnsw ((embedding::vector(D)) vector_cosine_ops) WHERE model = '<model>'
  -> đổi model = reindex song song (model cũ vẫn phục vụ), chuyển config + restart; rollback = đổi
  config về model cũ (embedding cũ còn nguyên). Xem kb/reindex.py.
- Pre-filter SQL thật trên cột của kb_chunks: role (chỉ child/single), deleted=false, doc_type,
  store_code (list có thể chứa None = kèm dữ liệu toàn hệ thống — scope theo store, D2), sku (&&),
  is_active / as_of (effective_date <= as_of < valid_to, C2), khoảng effective_date.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from index.store import Filters, SearchResult, VectorRecord, VectorStore, _as_list

logger = logging.getLogger(__name__)

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,200}$")


def _check_model(model: str) -> str:
    if not _MODEL_NAME_RE.match(model):
        raise ValueError(f"Tên embedding model không hợp lệ: {model!r}")
    return model


def model_index_name(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")[:40]
    return f"ix_chunk_emb_hnsw_{slug}_{hashlib.sha1(model.encode()).hexdigest()[:6]}"


def ensure_model_index(session: Session, model: str, dim: int) -> str:
    """Idempotent — tạo partial HNSW index cho model nếu chưa có (gọi đầu job reindex)."""
    model = _check_model(model)
    name = model_index_name(model)
    session.execute(text(
        f"CREATE INDEX IF NOT EXISTS {name} ON chunk_embeddings USING hnsw "
        f"((embedding::vector({int(dim)})) vector_cosine_ops) WHERE model = '{model}'"
    ))
    return name


def build_filter_sql(filters: Filters | None, alias: str = "c") -> tuple[str, dict[str, Any]]:
    """Filters -> (SQL fragment, params) trên bảng kb_chunks — luôn tham số hoá. Thuần, test được."""
    f = filters or Filters()
    clauses: list[str] = [f"{alias}.deleted = false", f"{alias}.role <> 'parent'"]
    params: dict[str, Any] = {}
    if f.doc_type is not None:
        clauses.append(f"{alias}.doc_type = ANY(:doc_types)")
        params["doc_types"] = _as_list(f.doc_type)
    if f.store_code is not None:
        stores = _as_list(f.store_code)
        concrete = [s for s in stores if s is not None]
        parts = []
        if concrete:
            parts.append(f"{alias}.store_code = ANY(:stores)")
            params["stores"] = concrete
        if None in stores:
            parts.append(f"{alias}.store_code IS NULL")
        clauses.append("(" + " OR ".join(parts) + ")" if parts else "false")
    if f.sku is not None:
        clauses.append(f"{alias}.skus && CAST(:skus AS text[])")
        params["skus"] = _as_list(f.sku)
    if f.as_of is not None:
        clauses.append(f"({alias}.effective_date IS NULL OR {alias}.effective_date <= CAST(:as_of AS date))")
        clauses.append(f"({alias}.valid_to IS NULL OR {alias}.valid_to > CAST(:as_of AS date))")
        params["as_of"] = str(f.as_of)
    elif f.is_active is not None:
        clauses.append(f"{alias}.is_active = :is_active")
        params["is_active"] = bool(f.is_active)
    if f.effective_date_from is not None:
        clauses.append(f"{alias}.effective_date >= CAST(:eff_from AS date)")
        params["eff_from"] = str(f.effective_date_from)
    if f.effective_date_to is not None:
        clauses.append(f"{alias}.effective_date <= CAST(:eff_to AS date)")
        params["eff_to"] = str(f.effective_date_to)
    return " AND ".join(clauses), params


def chunk_metadata_row(row: Any) -> dict[str, Any]:
    meta = dict(row.meta or {})
    meta.update({
        "record_id": row.record_id, "document_id": row.document_id, "parent_chunk_id": row.parent_chunk_id,
        "role": row.role, "doc_type": row.doc_type, "store_code": row.store_code, "sku": list(row.skus or []),
        "effective_date": row.effective_date.isoformat() if row.effective_date else None,
        "valid_to": row.valid_to.isoformat() if row.valid_to else None, "is_active": row.is_active,
        "location": row.location, "context_prefix": row.context_prefix,
    })
    return meta


class KBVectorStore(VectorStore):
    def __init__(self, sm: sessionmaker, model: str, dim: int, ef_search: int = 100) -> None:
        self._sm = sm
        self.model = _check_model(model)
        self.dim = int(dim)
        self.ef_search = int(ef_search)

    def upsert(self, records: list[VectorRecord]) -> None:
        """Ghi embedding cho chunk ĐÃ tồn tại trong kb_chunks (idempotent theo (chunk_id, model))."""
        if not records:
            return
        with self._sm() as s, s.begin():
            for r in records:
                s.execute(text(
                    "INSERT INTO chunk_embeddings (chunk_id, model, dim, embedding, text_hash, created_at) "
                    "VALUES (:cid, :model, :dim, CAST(:emb AS vector), :th, now()) "
                    "ON CONFLICT (chunk_id, model) DO UPDATE SET embedding = EXCLUDED.embedding, "
                    "dim = EXCLUDED.dim, text_hash = EXCLUDED.text_hash, created_at = now()"),
                    {"cid": r.id, "model": self.model, "dim": self.dim, "emb": _vec_literal(r.vector),
                     "th": (r.metadata or {}).get("text_hash", "")})

    def search(self, query_vec: list[float], top_k: int, filters: Filters | None = None) -> list[SearchResult]:
        where, params = build_filter_sql(filters)
        params.update({"q": _vec_literal(query_vec), "k": int(top_k)})
        sql = (
            f"SELECT c.chunk_id, c.text, c.record_id, c.document_id, c.parent_chunk_id, c.role, c.doc_type, "
            f"c.store_code, c.skus, c.effective_date, c.valid_to, c.is_active, c.location, c.context_prefix, "
            f"c.metadata AS meta, 1 - ((e.embedding::vector({self.dim})) <=> CAST(:q AS vector({self.dim}))) AS score "
            f"FROM chunk_embeddings e JOIN kb_chunks c ON c.chunk_id = e.chunk_id "
            f"WHERE e.model = '{self.model}' AND {where} "
            f"ORDER BY (e.embedding::vector({self.dim})) <=> CAST(:q AS vector({self.dim})) LIMIT :k"
        )
        with self._sm() as s, s.begin():
            s.execute(text(f"SET LOCAL hnsw.ef_search = {self.ef_search}"))
            rows = s.execute(text(sql), params).all()
        return [SearchResult(id=r.chunk_id, score=float(r.score), text=r.text, metadata=chunk_metadata_row(r)) for r in rows]

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._sm() as s, s.begin():
            s.execute(text("DELETE FROM chunk_embeddings WHERE model = :m AND chunk_id = ANY(:ids)"),
                      {"m": self.model, "ids": list(ids)})

    def count(self) -> int:
        with self._sm() as s:
            return int(s.execute(text("SELECT count(*) FROM chunk_embeddings WHERE model = :m"), {"m": self.model}).scalar_one())


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"
