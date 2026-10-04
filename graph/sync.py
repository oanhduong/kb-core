"""Cập nhật graph incremental (A6) + rebuild full.

sync_document(doc): sau khi 1 document ingest xong (hoặc bị thay thế/xoá/khôi phục) — CHỈ đụng phần
graph của document đó:
    1. store.remove_document(doc) — gỡ đóng góp cũ của doc (phần rule; thêm phần "~llm" nếu chạy LLM).
    2. doc còn active -> build_document(doc, context lấy từ graph hiện có) -> ghi merge vào store.
    3. Builder có phụ thuộc (DEPENDENTS, vd bảng giá đổi -> tồn kho/NCC/khuyến mãi phải resolve lại
       SKU) -> làm lại PHẦN RULE của các document phụ thuộc (không gọi LLM, không đụng cạnh LLM).
rebuild_full(): clear graph rồi build lần lượt mọi document active theo BUILD_ORDER — lệnh riêng
(`make graph-rebuild`, job graph_rebuild), dùng khi đổi logic builder hoặc graph hỏng.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_config
from core.logging import log_event
from core.models import Document, GraphDocument, KBRecordRow, utcnow
from graph.build import BUILD_ORDER, DEPENDENTS, BuildContext, DocumentInput, build_document, detect_builder, load_units
from graph.resolve import ALIAS_KINDS, invalidate_resolver_cache, load_aliases, normalize_text
from graph.store import GraphStore
from ingest.units import load_units_file

logger = logging.getLogger(__name__)


def units_cache_path(doc: Document) -> str:
    return os.path.join(os.path.dirname(doc.storage_path), "units.json")


def document_input(session: Session, doc: Document) -> DocumentInput:
    di = DocumentInput(document_id=doc.id, path=doc.storage_path, filename=doc.filename, source_type=doc.source_type,
                       doc_type=doc.doc_type, title=doc.title, series_key=doc.series_key,
                       units=load_units_file(units_cache_path(doc)))
    if doc.doc_type == "training" or doc.source_type in ("audio", "video", "pdf", "image"):
        rows = session.execute(select(KBRecordRow).where(KBRecordRow.document_id == doc.id,
                                                         KBRecordRow.deleted_at.is_(None))
                               .order_by(KBRecordRow.created_at, KBRecordRow.location)).scalars().all()
        di.records = [{"record_id": r.record_id, "content": r.content, "location": r.location} for r in rows]
    return di


def _product_aliases(session: Session, workspace_id: str) -> dict[str, str]:
    return {normalize_text(a.alias): normalize_text(a.canonical) for a in load_aliases(session, workspace_id) if a.kind == "product"}


def _record_state(session: Session, doc_id: str, builder: str, status: str, nodes: int = 0, edges: int = 0,
                  llm_edges: int = 0, warnings: list[str] | None = None, error: str | None = None,
                  workspace_id: str | None = None) -> None:
    row = session.get(GraphDocument, doc_id)
    if row is None:
        if workspace_id is None:
            return
        row = GraphDocument(document_id=doc_id, workspace_id=workspace_id, builder=builder, status=status)
        session.add(row)
    row.builder, row.status, row.node_count, row.edge_count = builder, status, nodes, edges
    row.llm_edges, row.warnings, row.error, row.updated_at = llm_edges, (warnings or [])[:50], error, utcnow()
    session.flush()


def sync_document(session: Session, store: GraphStore, document_id: str, *, llm: bool = True,
                  cascade: bool = True, extractor: Any = None) -> dict[str, Any]:
    cfg = get_config()
    doc = session.get(Document, document_id)
    removed = store.remove_document(document_id)
    if llm:
        removed_llm = store.remove_document(f"{document_id}~llm")
        removed = {k: removed.get(k, 0) + removed_llm.get(k, 0) for k in removed}
    if doc is None or not doc.is_active or doc.deleted_at is not None:
        _record_state(session, document_id, "none", "removed")
        invalidate_resolver_cache(doc.workspace_id if doc else None)
        log_event(logger, "graph_sync_removed", document_id=document_id, **removed)
        return {"document_id": document_id, "status": "removed", "removed": removed}

    di = document_input(session, doc)
    ctx = BuildContext.from_store(store, product_aliases=_product_aliases(session, doc.workspace_id))
    if llm and extractor is None and cfg.graph.llm_extraction.enabled and doc.doc_type in cfg.graph.llm_extraction.doc_types:
        from core.llm import get_llm
        from graph.extract import GraphExtractor

        extractor = GraphExtractor(get_llm(), cfg)
    result = build_document(di, ctx, extractor=extractor if llm else None)
    written = store.write_accumulator(result.acc) if result.builder != "none" else {"nodes": 0, "edges": 0}
    status = "done" if result.builder != "none" else "skipped"
    _record_state(session, document_id, result.builder, status, written["nodes"], written["edges"],
                  result.llm_edges, result.warnings, workspace_id=doc.workspace_id)
    out: dict[str, Any] = {"document_id": document_id, "status": status, "builder": result.builder,
                           "removed": removed, "written": written, "llm_edges": result.llm_edges,
                           "warnings": result.warnings[:20], "dependents": []}
    log_event(logger, "graph_sync_document", document_id=document_id, builder=result.builder,
              nodes=written["nodes"], edges=written["edges"], llm_edges=result.llm_edges, warnings=len(result.warnings))

    if cascade and result.builder in DEPENDENTS:
        dep_builders = set(DEPENDENTS[result.builder])
        deps = session.execute(select(GraphDocument.document_id).where(
            GraphDocument.workspace_id == doc.workspace_id, GraphDocument.builder.in_(dep_builders),
            GraphDocument.status == "done", GraphDocument.document_id != document_id)).scalars().all()
        for dep_id in deps:
            out["dependents"].append(sync_document(session, store, dep_id, llm=False, cascade=False)["document_id"])
    invalidate_resolver_cache(doc.workspace_id)
    return out


def rebuild_full(session: Session, store: GraphStore, workspace_id: str, *, llm: bool = True,
                 progress: Any = None) -> dict[str, Any]:
    """Xoá sạch graph của workspace rồi build lại mọi document active theo BUILD_ORDER."""
    store.clear()
    for row in session.execute(select(GraphDocument).where(GraphDocument.workspace_id == workspace_id)).scalars().all():
        session.delete(row)
    session.flush()
    docs = session.execute(select(Document).where(Document.workspace_id == workspace_id, Document.is_active.is_(True),
                                                  Document.deleted_at.is_(None))).scalars().all()
    planned: list[tuple[int, Document]] = []
    for d in docs:
        di = document_input(session, d)
        units = load_units(di) if d.source_type in ("xlsx", "csv", "docx") else []
        b = detect_builder(di, units)
        planned.append((BUILD_ORDER.index(b) if b in BUILD_ORDER else 99, d))
    planned.sort(key=lambda x: (x[0], x[1].created_at))
    results = []
    for i, (_, d) in enumerate(planned):
        results.append(sync_document(session, store, d.id, llm=llm, cascade=False))
        session.commit()
        if progress:
            progress(i + 1, len(planned))
    stats = store.stats()
    log_event(logger, "graph_rebuild_full", documents=len(planned), **{k: stats[k] for k in ("node_count", "edge_count")})
    return {"documents": len(planned), "results": [{"document_id": r["document_id"], "builder": r.get("builder"),
                                                     "status": r["status"]} for r in results], "graph": stats}


__all__ = ["sync_document", "rebuild_full", "document_input", "units_cache_path", "ALIAS_KINDS"]
