"""BM25 in-process cache trên toàn bộ chunk retrievable (child/single, chưa xoá) — tự làm mới khi KB đổi.

KB có ingest incremental liên tục nên BM25Index (IDF tính 1 lần lúc dựng) phải dựng lại khi dữ liệu
đổi: mỗi lần ghi KB (ingest/sửa/xoá/khôi phục) bump kb_state.version (kb/repository.bump_kb_version).
get() kiểm tra version tối đa mỗi `refresh_seconds`; version đổi -> dựng index MỚI ở luồng nền và
tráo vào khi xong, request trong lúc dựng vẫn dùng index cũ (không chặn câu hỏi). Lần đầu (chưa có
index) thì dựng đồng bộ.

Index chứa cả chunk inactive (bảng giá cũ) để câu hỏi "giá tại thời điểm" (filter as_of) vẫn tìm
được — lọc is_active/as_of/store do _matches_filters đảm nhiệm trên metadata của từng chunk.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from index.hybrid import BM25Index
from index.store import VectorRecord

logger = logging.getLogger(__name__)


def kb_version(sm: sessionmaker) -> int:
    with sm() as s:
        v = s.execute(text("SELECT version FROM kb_state WHERE id = 1")).scalar()
        return int(v or 0)


def load_bm25_documents(sm: sessionmaker) -> list[VectorRecord]:
    sql = ("SELECT chunk_id, text, context_prefix, workspace_id, record_id, document_id, parent_chunk_id, role, doc_type, "
           "store_code, skus, effective_date, valid_to, is_active, location FROM kb_chunks "
           "WHERE deleted = false AND role <> 'parent'")
    docs = []
    with sm() as s:
        for r in s.execute(text(sql)):
            body = f"{r.context_prefix}\n{r.text}" if r.context_prefix else r.text
            docs.append(VectorRecord(id=r.chunk_id, vector=None, text=body, metadata={
                "workspace_id": r.workspace_id, "record_id": r.record_id, "document_id": r.document_id, "parent_chunk_id": r.parent_chunk_id,
                "role": r.role, "doc_type": r.doc_type, "store_code": r.store_code, "sku": list(r.skus or []),
                "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None, "is_active": r.is_active,
                "location": r.location, "raw_text": r.text,
            }))
    return docs


class BM25Cache:
    def __init__(self, sm: sessionmaker, refresh_seconds: float = 30.0) -> None:
        self._sm = sm
        self._refresh = refresh_seconds
        self._index: BM25Index | None = None
        self._version: int | None = None
        self._checked_at = 0.0
        self._lock = threading.Lock()
        self._building = False

    def _build(self, version: int) -> None:
        start = time.perf_counter()
        docs = load_bm25_documents(self._sm)
        index = BM25Index(docs)
        with self._lock:
            self._index, self._version = index, version
            self._building = False
        logger.info("bm25 index dựng lại: %d chunk, version=%d, %.2fs", len(docs), version, time.perf_counter() - start)

    def _build_safe(self, version: int) -> None:
        try:
            self._build(version)
        except Exception:
            logger.exception("bm25 rebuild lỗi — giữ index cũ")
            with self._lock:
                self._building = False

    def get(self) -> BM25Index:
        now = time.monotonic()
        if self._index is not None and now - self._checked_at < self._refresh:
            return self._index
        self._checked_at = now
        version = kb_version(self._sm)
        if self._index is None:
            self._build(version)
            return self._index  # type: ignore[return-value]
        if version != self._version:
            with self._lock:
                start_bg = not self._building
                self._building = True
            if start_bg:
                threading.Thread(target=self._build_safe, args=(version,), daemon=True, name="bm25-rebuild").start()
        return self._index

    def stats(self) -> dict[str, Any]:
        return {"version": self._version, "documents": len(self._index._documents) if self._index else 0}  # noqa: SLF001
