"""Công thức định danh dùng chung giữa ingest (kb_records) và graph (source_chunk_ids).

record_id = "<document_id>:<location>" — xác định hoàn toàn từ (document, vị trí trong file), nên:
- ingest chạy lại cùng 1 file -> cùng record_id -> upsert idempotent, không nhân bản;
- graph/build.py tính ra ĐÚNG record_id của dòng xlsx / Điều docx mà không cần đọc DB -> node/edge
  trỏ thẳng về "chunk gốc" (đơn vị nội dung gốc: 1 dòng bảng, 1 Điều chính sách, 1 đoạn transcript).
  kb_chunks (child/parent dùng cho retrieval) sinh ra từ record này, tra qua kb_chunks.record_id —
  đổi chiến lược chunking (reindex) không làm graph mất liên kết.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def record_id_for(document_id: str, location: str, ordinal: int | None = None) -> str:
    loc = _WS_RE.sub(" ", unicodedata.normalize("NFC", location)).strip()
    base = f"{document_id}:{loc}"
    return base if not ordinal else f"{base}#{ordinal}"


def document_id_of(record_id: str) -> str:
    return record_id.split(":", 1)[0]


def normalize_content_for_hash(text: str) -> str:
    """Chuẩn hoá trước khi băm để dedup (C3): NFC, gộp khoảng trắng, bỏ hoa/thường."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text or "")).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_content_for_hash(text).encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
