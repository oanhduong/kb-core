"""Job knowledge_extract — tri thức mở cho 1 tài liệu (chạy sau ingest):
    1. bảng số liệu -> data_tables/parquet (analytics)  [analytics/tables.py]
    2. hồ sơ ngữ nghĩa + tóm tắt tài liệu (LLM)          [knowledge/profile.py]
    3. entity/quan hệ mở (LLM) + hợp nhất theo workspace  [knowledge/extract.py]
Mỗi bước độc lập: bước lỗi được ghi vào kết quả job, không chặn bước khác. Bước 1 không cần LLM.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig
from core.db import session_scope
from core.logging import log_event
from core.models import DataTable, Document

logger = logging.getLogger(__name__)

Progress = Callable[..., None]


def extract_document_knowledge(cfg: AppConfig, sm: sessionmaker, llm: Any, embedder: Any, document_id: str,
                               progress: Progress | None = None, only: list[str] | None = None, force: bool = False) -> dict[str, Any]:
    progress = progress or (lambda *a, **k: None)
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None or doc.deleted_at is not None:
            return {"skipped": "tài liệu không tồn tại/đã xoá"}
        if doc.status != "active":
            return {"skipped": f"tài liệu ở trạng thái {doc.status}"}
    steps = only or ["tables", "profile", "entities", "translate"]
    out: dict[str, Any] = {"document_id": document_id, "steps": {}}
    total = len(steps)
    for i, step in enumerate(steps):
        progress(i, total, step)
        try:
            if step == "tables":
                from analytics.tables import extract_tables_for_document

                out["steps"]["tables"] = extract_tables_for_document(cfg, sm, llm, document_id, force=force)
            elif step == "profile":
                from knowledge.profile import build_document_profile

                out["steps"]["profile"] = build_document_profile(cfg, sm, llm, document_id, force=force)
            elif step == "entities":
                from knowledge.extract import extract_entities_for_document

                out["steps"]["entities"] = extract_entities_for_document(cfg, sm, llm, document_id, force=force)
            elif step == "translate":
                from knowledge.translate import translate_document

                out["steps"]["translate"] = translate_document(cfg, sm, llm, document_id, force=force)
            elif step == "chunks_meta":
                out["steps"]["chunks_meta"] = {"ok": True}
        except Exception as e:  # bước nào lỗi ghi lại, các bước khác vẫn chạy
            logger.exception("knowledge step %s lỗi", step)
            out["steps"][step] = {"error": f"{type(e).__name__}: {e}"[:500]}
    progress(total, total, "done")
    # bảng mới / thực thể vừa trích lại -> dò lại quan hệ giữa các bảng của workspace + thực thể từ cột mã
    # (sau bước entities: trích lại xoá mention của tài liệu, kể cả mention dựng từ cột mã)
    if any(st in out["steps"] and "error" not in out["steps"][st] and "skipped" not in out["steps"][st] for st in ("tables", "entities")):
        try:
            from analytics.relations import schedule_discovery

            with session_scope() as s:
                ws = s.get(Document, document_id).workspace_id
                if s.execute(select(func.count()).select_from(DataTable).where(DataTable.workspace_id == ws, DataTable.is_active.is_(True))).scalar_one() >= 2:
                    schedule_discovery(s, ws)
        except Exception:
            logger.exception("không đẩy được job dò quan hệ bảng")
    log_event(logger, "knowledge_extract_done", document_id=document_id,
              **{k: ("error" if isinstance(v, dict) and "error" in v else "ok") for k, v in out["steps"].items()})
    return out
