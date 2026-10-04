"""Reindex job (C5): chạy lại embedding khi ĐỔI EMBEDDING MODEL hoặc ĐỔI CHUNKING CONFIG.

- Phạm vi: toàn bộ, hoặc subset theo doc_type / document_id (payload job).
- rechunk: "auto" (mặc định) = chỉ sinh lại chunk cho record có chunking_fingerprint khác config hiện
  tại; true = sinh lại hết trong phạm vi; false = chỉ embed lại.
- model: mặc định model trong config; có thể chỉ định model KHÁC để chuẩn bị trước khi chuyển config
  (embedding của model mới nằm song song, model cũ vẫn phục vụ tới khi đổi config + restart).
- Idempotent + resume: embed_chunks bỏ qua chunk đã có embedding với cùng text_hash; job chết giữa
  chừng chạy lại chỉ làm phần còn thiếu. Progress cập nhật theo batch (GET /ingest/{job_id}).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig
from core.db import session_scope
from core.logging import log_event
from core.models import ChunkRow, Document, KBRecordRow
from kb.chunking import embed_chunks, rebuild_chunks
from kb.repository import bump_kb_version

logger = logging.getLogger(__name__)


def run_reindex(cfg: AppConfig, sm: sessionmaker, embedder: Any, payload: dict[str, Any],
                progress: Callable[[int, int, str], None]) -> dict[str, Any]:
    doc_types = payload.get("doc_types") or ([payload["doc_type"]] if payload.get("doc_type") else None)
    document_ids = payload.get("document_ids") or ([payload["document_id"]] if payload.get("document_id") else None)
    rechunk = payload.get("rechunk", "auto")
    model = payload.get("model")
    if model and model != embedder.model_name:
        from index.embeddings import SentenceTransformerEmbedder

        dim = int(payload.get("dim") or cfg.embedding.dim)
        embedder = SentenceTransformerEmbedder(model, dim, cfg.embedding.query_prefix, cfg.embedding.passage_prefix,
                                               cfg.embedding.batch_size, cfg.embedding.device)
    fp = cfg.chunking.fingerprint()
    rechunked_records = 0
    with session_scope() as s:
        q = select(Document).where(Document.deleted_at.is_(None))
        if payload.get("workspace_id"):
            q = q.where(Document.workspace_id == payload["workspace_id"])
        if document_ids:
            q = q.where(Document.id.in_(document_ids))
        if doc_types:
            q = q.where(Document.doc_type.in_(doc_types))
        docs = list(s.execute(q).scalars())
        doc_ids = [d.id for d in docs]
        if rechunk in (True, "true", "auto"):
            for i, d in enumerate(docs):
                recs = list(s.execute(select(KBRecordRow).where(KBRecordRow.document_id == d.id)).scalars())
                if rechunk == "auto":
                    stale = set(s.execute(select(ChunkRow.record_id).where(ChunkRow.document_id == d.id,
                                                                           ChunkRow.chunking_fingerprint != fp)).scalars())
                    missing = {r.record_id for r in recs} - set(s.execute(select(ChunkRow.record_id).where(ChunkRow.document_id == d.id)).scalars())
                    recs = [r for r in recs if r.record_id in stale or r.record_id in missing]
                if recs:
                    rebuild_chunks(s, cfg, d, recs)
                    rechunked_records += len(recs)
                progress(i + 1, len(docs), "rechunk")
            if rechunked_records:
                bump_kb_version(s)
    if not doc_ids:
        return {"documents": 0, "rechunked_records": 0, "embedded": 0, "skipped_up_to_date": 0}
    emb = embed_chunks(sm, cfg, embedder, document_ids=doc_ids, model=embedder.model_name,
                       force=bool(payload.get("force")), progress=lambda d, t: progress(d, t, "embed"))
    with session_scope() as s:
        bump_kb_version(s)
    out = {"documents": len(doc_ids), "rechunked_records": rechunked_records, "chunking_fingerprint": fp, **emb}
    log_event(logger, "reindex_done", **{k: v for k, v in out.items() if k != "model"}, model=emb["model"])
    return out
