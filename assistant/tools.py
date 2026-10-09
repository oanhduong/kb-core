"""Tool của assistant — schema cho Anthropic tool use + hiện thực chạy trên DB/retriever/engine của workspace.

Mỗi tool trả (text cho model, danh sách Source đã đăng ký, artifact cho UI). Text đưa vào model luôn bọc
<untrusted_document> cho phần nội dung tài liệu. Tool KHÔNG bao giờ raise ra ngoài: lỗi -> text lỗi
ngắn để model đổi cách (và được ghi vào decision trace).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from analytics import engine
from analytics.charts import build_chart
from analytics.relations import join_hints
from assistant.sources import Source, SourceRegistry
from core.config import AppConfig
from core.errors import AppError
from core.models import DataTable, Document, Entity, EntityMention, KBRecordRow
from core.principal import WorkspaceContext
from index.store import Filters
from kb import repository as repo
from knowledge import service as ks
from observability import tracing
from qa.abstention import decide
from qa.citation import format_citation
from qa.retrieval import VectorRetriever
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "search_documents",
     "description": "Tìm các đoạn văn bản liên quan trong knowledge base (hybrid semantic + từ khoá, có rerank). Dùng cho hỏi đáp, tra cứu chi tiết, "
                    "trích dẫn chính sách/quy định/nội dung. Có thể giới hạn trong vài tài liệu bằng document_ids.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Câu truy vấn (tiếng Việt, đủ ngữ cảnh, có thể kèm mã/tên cụ thể)"},
         "document_ids": {"type": "array", "items": {"type": "string"}, "description": "Chỉ tìm trong các tài liệu này (tuỳ chọn)"},
         "top_k": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Số đoạn muốn lấy (mặc định 6)"}},
         "required": ["query"]}},
    {"name": "list_documents",
     "description": "Liệt kê tài liệu trong workspace (tên, loại, tóm tắt, chủ đề, số bảng số liệu). Dùng khi hỏi 'có những tài liệu gì', "
                    "tìm tài liệu theo chủ đề, hoặc trước khi tóm tắt nhiều tài liệu.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Lọc theo tên/tóm tắt (tuỳ chọn)"},
         "doc_type": {"type": "string", "description": "Lọc theo loại (tuỳ chọn)"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "get_document_overview",
     "description": "Tổng quan 1 tài liệu: tóm tắt, hồ sơ ngữ nghĩa (loại, chủ đề, khoảng thời gian, cấu trúc), dàn ý các mục, bảng số liệu và "
                    "thực thể chính. Dùng để tóm tắt/giải thích tài liệu đó nói về gì.",
     "input_schema": {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]}},
    {"name": "list_tables",
     "description": "Catalog bảng số liệu có thể truy vấn SQL (tên bảng, cột + kiểu, mô tả, dòng mẫu). GỌI TRƯỚC khi viết SQL.",
     "input_schema": {"type": "object", "properties": {"document_ids": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "query_tables",
     "description": "Chạy MỘT câu SQL DuckDB read-only (SELECT/WITH) trên các bảng trong catalog để thống kê, tính toán, xếp hạng, xu hướng. "
                    "Kết quả có thể kèm biểu đồ (chart). Tên bảng/cột phải đúng như catalog.",
     "input_schema": {"type": "object", "properties": {
         "sql": {"type": "string"},
         "purpose": {"type": "string", "description": "Câu hỏi/ý định của truy vấn (hiện cho người dùng)"},
         "chart": {"type": "object", "description": "Gợi ý biểu đồ: {type: bar|line|area|pie|scatter|table|kpi, x, y, series, title}",
                   "properties": {"type": {"type": "string"}, "x": {"type": "string"}, "y": {"type": ["string", "array"]},
                                  "series": {"type": "string"}, "title": {"type": "string"}}}},
         "required": ["sql"]}},
    {"name": "explore_graph",
     "description": "Tìm thực thể theo tên và lấy các mối quan hệ của nó (ai liên quan tới ai/cái gì, qua quan hệ gì, bằng chứng trong tài liệu nào). "
                    "Dùng cho câu hỏi về mối liên hệ, mạng lưới, 'X liên quan gì tới Y'.",
     "input_schema": {"type": "object", "properties": {
         "entity": {"type": "string", "description": "Tên (hoặc một phần tên) thực thể"},
         "depth": {"type": "integer", "minimum": 1, "maximum": 2},
         "relation_type": {"type": "string", "description": "Lọc loại quan hệ (tuỳ chọn)"}},
         "required": ["entity"]}},
    {"name": "read_source",
     "description": "Đọc toàn văn một nguồn đã trả về trước đó (theo mã S#) hoặc một đoạn/record theo record_id — khi cần chi tiết hơn đoạn trích.",
     "input_schema": {"type": "object", "properties": {"source_id": {"type": "string", "description": "Mã S# hoặc record_id"}}, "required": ["source_id"]}},
]


@dataclass
class ToolOutcome:
    text: str
    sources: list[Source] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    top_score: float | None = None
    score_kind: str | None = None


@dataclass
class ToolContext:
    cfg: AppConfig
    session: Session
    ctx: WorkspaceContext
    retriever: VectorRetriever
    registry: SourceRegistry
    scope: str | None = None  # pack retail: store_code
    document_ids: list[str] | None = None  # giới hạn mặc định từ request


def _wrap(src: Source, max_chars: int = 1400) -> str:
    body = src.text if len(src.text) <= max_chars else src.text[:max_chars] + " […]"
    meta = f' hiệu lực="{src.effective_date}"' if src.effective_date else ""
    return f"[{src.sid}] {src.label}{meta}\n" + wrap_untrusted(body, source=src.label)


def _doc_scope_ok(tc: ToolContext, store_code: str | None) -> bool:
    return tc.scope is None or store_code is None or store_code == tc.scope


# =====================================================================================
# search_documents
# =====================================================================================

def tool_search_documents(tc: ToolContext, query: str, document_ids: list[str] | None = None, top_k: int = 6) -> ToolOutcome:
    top_k = max(1, min(int(top_k or 6), 12))
    doc_ids = document_ids or tc.document_ids or None
    filters = Filters(workspace_id=tc.ctx.workspace_id, store_code=[tc.scope, None] if tc.scope else None, is_active=True,
                      document_ids=doc_ids)
    outcome = tc.retriever.retrieve(query, filters)
    # Ngưỡng căn cứ (B3): điểm rerank/dense của ứng viên tốt nhất quá thấp -> coi như không có nguồn liên quan,
    # để model nói "không tìm thấy" thay vì gượng ép trích dẫn đoạn không liên quan.
    codes = {c.upper() for c in re.findall(r"\b[A-Za-zĐđ]{2,5}-?\d{2,6}\b", query)}
    strong_bm25 = int(any(code in (c.text or "").upper() for c in outcome.candidates[:3] if c.bm25_score for code in codes))
    verdict = decide(tc.cfg, candidates=len(outcome.candidates), rerank_top=outcome.rerank_top if outcome.rerank_used else None,
                     dense_top=outcome.dense_top, bm25_hits=strong_bm25)
    if verdict.abstain:
        return ToolOutcome("Không tìm thấy đoạn nào đủ liên quan trong knowledge base (điểm liên quan quá thấp).",
                           summary={"hits": 0, "reason": verdict.reason, "top_score": verdict.top_score}, score_kind=verdict.score_kind)
    child_ids = [c.chunk_id for c in outcome.candidates][:top_k * 2]
    chunks = repo.context_chunks_for_child_ids(tc.session, child_ids)[:top_k]
    if not chunks:
        return ToolOutcome("Không tìm thấy đoạn nào liên quan trong knowledge base.", summary={"hits": 0}, score_kind="rerank" if outcome.rerank_used else "dense")
    docs = repo.get_documents(tc.session, {c.document_id for c in chunks})
    children = repo.get_chunks(tc.session, child_ids)
    score_by_parent: dict[str, float] = {}
    for c in outcome.candidates:
        ch = children.get(c.chunk_id)
        target = ch.parent_chunk_id if ch and ch.role == "child" and ch.parent_chunk_id else c.chunk_id
        sc = c.rerank_score if c.rerank_score is not None else (c.dense_score or 0.0)
        score_by_parent[target] = max(score_by_parent.get(target, 0.0), sc or 0.0)
    sources = []
    for c in chunks:
        d = docs.get(c.document_id)
        title = d.title if d else c.meta.get("source_file", "tài liệu")
        label = format_citation(title, d.source_type if d else "", c.location, c.meta)
        sources.append(tc.registry.add("chunk", label, c.text, chunk_id=c.chunk_id, record_id=c.record_id, document_id=c.document_id,
                                       location=c.location, effective_date=c.effective_date.isoformat() if c.effective_date else None,
                                       score=score_by_parent.get(c.chunk_id), origin="search_documents",
                                       extra={"store_code": c.store_code, "is_active": c.is_active}))
    top = outcome.rerank_top if outcome.rerank_used else outcome.dense_top
    text = f"{len(sources)} đoạn liên quan (xếp theo độ liên quan):\n\n" + "\n\n".join(_wrap(s) for s in sources)
    return ToolOutcome(text, sources, summary={"hits": len(sources), "dense": outcome.dense_count, "bm25": outcome.bm25_count,
                                               "rerank": outcome.rerank_used, "degraded": outcome.degraded},
                       top_score=top, score_kind="rerank" if outcome.rerank_used else "dense")


# =====================================================================================
# list_documents / get_document_overview
# =====================================================================================

def tool_list_documents(tc: ToolContext, query: str | None = None, doc_type: str | None = None, limit: int = 20) -> ToolOutcome:
    q = select(Document).where(Document.workspace_id == tc.ctx.workspace_id, Document.deleted_at.is_(None), Document.is_active.is_(True))
    if tc.scope:
        q = q.where(or_(Document.store_code == tc.scope, Document.store_code.is_(None)))
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    if query:
        q = q.where(or_(Document.title.ilike(f"%{query}%"), Document.filename.ilike(f"%{query}%"), Document.summary.ilike(f"%{query}%")))
    docs = tc.session.execute(q.order_by(Document.created_at.desc()).limit(max(1, min(int(limit or 20), 50)))).scalars().all()
    total = tc.session.execute(select(func.count()).select_from(Document).where(Document.workspace_id == tc.ctx.workspace_id,
                                                                                 Document.deleted_at.is_(None), Document.is_active.is_(True))).scalar_one()
    if not docs:
        return ToolOutcome("Workspace chưa có tài liệu nào phù hợp.", summary={"total": int(total)})
    lines = [f"Workspace có {total} tài liệu đang hiệu lực" + (f", {len(docs)} khớp bộ lọc" if query or doc_type else "") + ":"]
    sources = []
    for d in docs:
        prof = d.semantic_profile or {}
        desc = (d.summary or "").strip()
        s = tc.registry.add("document", d.title, desc or f"{d.filename} ({d.source_type})", document_id=d.id, location=d.filename,
                            effective_date=d.effective_date.isoformat() if d.effective_date else None, origin="list_documents",
                            extra={"doc_type": d.doc_type, "kind": prof.get("kind"), "topics": prof.get("topics", []), "table_count": d.table_count})
        sources.append(s)
        lines.append(f"[{s.sid}] id={d.id} · {d.title} · {d.source_type}/{d.doc_type}" + (f" · {prof.get('kind')}" if prof.get("kind") else "")
                     + (f" · hiệu lực {d.effective_date}" if d.effective_date else "") + (f" · {d.table_count} bảng số liệu" if d.table_count else "")
                     + (f"\n    chủ đề: {', '.join(prof.get('topics', [])[:6])}" if prof.get("topics") else "")
                     + (f"\n    {wrap_untrusted(desc[:400], source=d.title)}" if desc else ""))
    return ToolOutcome("\n".join(lines), sources, summary={"total": int(total), "listed": len(docs)})


def tool_get_document_overview(tc: ToolContext, document_id: str) -> ToolOutcome:
    d = tc.session.get(Document, document_id)
    if d is None or d.workspace_id != tc.ctx.workspace_id or d.deleted_at is not None or not _doc_scope_ok(tc, d.store_code):
        return ToolOutcome(f"Không có tài liệu id={document_id} trong workspace.", error="not_found")
    prof = d.semantic_profile or {}
    recs = tc.session.execute(select(KBRecordRow.record_id, KBRecordRow.location, KBRecordRow.content)
                              .where(KBRecordRow.document_id == d.id, KBRecordRow.deleted_at.is_(None), KBRecordRow.is_active.is_(True))
                              .order_by(KBRecordRow.created_at, KBRecordRow.record_id).limit(400)).all()
    tables = tc.session.execute(select(DataTable).where(DataTable.document_id == d.id, DataTable.is_active.is_(True))).scalars().all()
    ents = tc.session.execute(select(Entity.name, Entity.type, func.count(EntityMention.id)).join(EntityMention, EntityMention.entity_id == Entity.id)
                              .where(EntityMention.document_id == d.id).group_by(Entity.id).order_by(func.count(EntityMention.id).desc()).limit(15)).all()
    sources = []
    main = tc.registry.add("document", d.title, d.summary or "", document_id=d.id, location=d.filename,
                           effective_date=d.effective_date.isoformat() if d.effective_date else None, origin="get_document_overview",
                           extra={"doc_type": d.doc_type, "kind": prof.get("kind")})
    sources.append(main)
    out = [f"[{main.sid}] {d.title} ({d.filename}, {d.source_type}/{d.doc_type}" + (f", hiệu lực {d.effective_date}" if d.effective_date else "") + ")"]
    if d.summary:
        out.append("Tóm tắt: " + wrap_untrusted(d.summary, source=d.title))
    for k, lbl in (("kind", "Loại"), ("audience", "Đối tượng"), ("structure", "Cấu trúc"), ("time_range", "Thời gian"), ("quality_notes", "Lưu ý chất lượng")):
        if prof.get(k):
            out.append(f"{lbl}: {prof[k]}")
    if prof.get("topics"):
        out.append("Chủ đề: " + ", ".join(prof["topics"]))
    # dàn ý: location của các mục (docx: Điều; pdf: trang; xlsx: sheet)
    seen_locs: list[str] = []
    for rid, loc, content in recs:
        key = loc.split(" > ")[-1] if " > " in loc else loc.split("!")[0]
        if key not in seen_locs:
            seen_locs.append(key)
    if seen_locs:
        out.append(f"Dàn ý ({len(seen_locs)} mục): " + "; ".join(seen_locs[:40]) + (" …" if len(seen_locs) > 40 else ""))
    # vài đoạn đầu để model có nội dung thật khi tóm tắt (mỗi đoạn là 1 nguồn trích dẫn được)
    for rid, loc, content in recs[:6]:
        s = tc.registry.add("chunk", format_citation(d.title, d.source_type, loc), content, record_id=rid, document_id=d.id, location=loc,
                            effective_date=d.effective_date.isoformat() if d.effective_date else None, origin="get_document_overview")
        sources.append(s)
        out.append(_wrap(s, 900))
    if tables:
        out.append("Bảng số liệu: " + "; ".join(f"{t.name} ({t.row_count} dòng: {', '.join(c['name'] for c in (t.columns or [])[:8])})" for t in tables))
    if ents:
        out.append("Thực thể chính: " + ", ".join(f"{n} ({t}, {c} lần)" for n, t, c in ents))
    return ToolOutcome("\n".join(out), sources, summary={"records": len(recs), "tables": len(tables), "entities": len(ents)})


# =====================================================================================
# list_tables / query_tables
# =====================================================================================

def tool_list_tables(tc: ToolContext, document_ids: list[str] | None = None) -> ToolOutcome:
    tables = engine.catalog(tc.session, tc.ctx.workspace_id, document_ids=document_ids or tc.document_ids)
    if not tables:
        return ToolOutcome("Workspace chưa có bảng số liệu nào (chưa upload bảng tính hoặc tài liệu không có bảng).", summary={"tables": 0})
    joins = join_hints(tc.session, tables)
    return ToolOutcome(engine.catalog_prompt(tables, joins), summary={"tables": len(tables), "names": [t.name for t in tables][:30], "joins": len(joins)})


def tool_query_tables(tc: ToolContext, sql: str, purpose: str | None = None, chart: dict[str, Any] | None = None) -> ToolOutcome:
    tables = engine.catalog(tc.session, tc.ctx.workspace_id)
    try:
        res = engine.run_query(tc.cfg, tables, sql, limit=200)
    except AppError as e:
        return ToolOutcome(f"SQL lỗi: {e.message}. Hãy kiểm tra lại tên bảng/cột trong catalog và sửa.", error=e.message[:300], summary={"sql": sql})
    used = [t for t in tables if t.name in (sql or "").lower()]
    label = "Truy vấn số liệu trên " + (", ".join(t.title for t in used[:3]) if used else "bảng dữ liệu")
    names = [c["name"] for c in res.columns]
    preview_rows = res.rows[:50]
    body = "\n".join(" | ".join("" if v is None else str(v) for v in r) for r in preview_rows)
    text_for_source = f"SQL: {res.sql}\n{' | '.join(names)}\n{body}"
    src = tc.registry.add("table_query", label, text_for_source, origin="query_tables", location=res.sql[:200],
                          document_id=used[0].document_id if used else "",
                          extra={"sql": res.sql, "table": [t.name for t in used], "row_count": res.row_count, "columns": names, "purpose": purpose})
    chart_obj = build_chart(res, chart, tc.cfg.analytics.chart_max_points)
    artifact = {"type": "query_result", "sid": src.sid, "sql": res.sql, "purpose": purpose, "columns": res.columns, "rows": res.rows,
                "row_count": res.row_count, "truncated": res.truncated, "chart": chart_obj, "tables": [t.name for t in used],
                "citation": label}
    text = (f"[{src.sid}] {label} — {res.row_count} dòng" + (" (đã cắt bớt)" if res.truncated else "") + f", {res.elapsed_ms}ms\n"
            f"Cột: {', '.join(names)}\n{body}" + ("\n(chỉ hiện 50 dòng đầu)" if res.row_count > 50 else "")
            + (f"\nBiểu đồ đề xuất: {chart_obj['type']}" if chart_obj else ""))
    return ToolOutcome(text, [src], [artifact], summary={"sql": res.sql, "rows": res.row_count, "tables": [t.name for t in used]},
                       top_score=1.0, score_kind="sql")


# =====================================================================================
# explore_graph
# =====================================================================================

def tool_explore_graph(tc: ToolContext, entity: str, depth: int = 1, relation_type: str | None = None) -> ToolOutcome:
    found = ks.list_entities(tc.session, tc.ctx.workspace_id, q=entity, limit=5)["items"]
    if not found:
        return ToolOutcome(f"Không có thực thể nào khớp '{entity}' trong đồ thị tri thức.", summary={"entity": entity, "hits": 0})
    seed = found[0]
    nb = ks.neighborhood(tc.session, tc.ctx.workspace_id, [seed["id"]], depth=max(1, min(int(depth or 1), 2)), limit_nodes=80)
    by_id = {n["id"]: n for n in nb["nodes"]}
    edges = [e for e in nb["edges"] if not relation_type or e["type"] == relation_type]
    recs = repo.get_records(tc.session, [e["record_id"] for e in edges])
    docs = repo.get_documents(tc.session, {r.document_id for r in recs.values()})
    sources = []
    ent_src = tc.registry.add("entity", f"thực thể {seed['name']} ({seed['type']})",
                              seed.get("description") or f"{seed['name']} được nhắc {seed['mention_count']} lần trong {seed['document_count']} tài liệu",
                              origin="explore_graph", extra={"entity_id": seed["id"]})
    sources.append(ent_src)
    lines = [f"[{ent_src.sid}] Thực thể: {seed['name']} ({seed['type']}) — {seed['mention_count']} lần nhắc, {seed['document_count']} tài liệu"
             + (f". Khớp khác: {', '.join(f['name'] for f in found[1:])}" if len(found) > 1 else "")]
    if seed.get("description"):
        lines.append("Mô tả: " + wrap_untrusted(seed["description"], source=seed["name"]))
    for e in edges[:40]:
        s_, t_ = by_id.get(e["source"]), by_id.get(e["target"])
        if not s_ or not t_:
            continue
        rec = recs.get(e["record_id"])
        d = docs.get(rec.document_id) if rec else None
        label = format_citation(d.title, d.source_type, rec.location) if d and rec else "nguồn quan hệ"
        src = tc.registry.add("relation", label, e.get("evidence") or (rec.content if rec else ""), record_id=e["record_id"],
                              document_id=e.get("document_id", ""), location=rec.location if rec else "", origin="explore_graph",
                              extra={"relation": e["type"], "source_entity": s_["name"], "target_entity": t_["name"]})
        sources.append(src)
        lines.append(f"[{src.sid}] {s_['name']} —{e['label']} ({e['type']})→ {t_['name']} (tin cậy {e['confidence']:.1f}; {label})"
                     + (f"\n    bằng chứng: {wrap_untrusted(e['evidence'][:300], source=label)}" if e.get("evidence") else ""))
    if len(edges) == 0:
        lines.append("Thực thể này chưa có quan hệ nào được trích.")
    return ToolOutcome("\n".join(lines), sources, [{"type": "graph", "seed": seed["id"], "nodes": nb["nodes"], "edges": edges}],
                       summary={"entity": seed["name"], "edges": len(edges), "nodes": len(nb["nodes"])}, top_score=0.8 if edges else None,
                       score_kind="graph")


# =====================================================================================
# read_source
# =====================================================================================

def tool_read_source(tc: ToolContext, source_id: str) -> ToolOutcome:
    src = tc.registry.get(source_id.strip().upper()) if source_id.strip().upper().startswith("S") else None
    record_id = src.record_id if src and src.record_id else (None if src else source_id)
    if not record_id:
        return ToolOutcome(f"Nguồn {source_id} không có record để đọc thêm.", error="no_record")
    rec = tc.session.get(KBRecordRow, record_id)
    if rec is None or rec.workspace_id != tc.ctx.workspace_id or rec.deleted_at is not None or not _doc_scope_ok(tc, rec.store_code):
        return ToolOutcome("Không tìm thấy record.", error="not_found")
    d = tc.session.get(Document, rec.document_id)
    label = format_citation(d.title if d else rec.source_file, d.source_type if d else rec.source_type, rec.location)
    s = tc.registry.add("chunk", label, rec.content, record_id=rec.record_id, document_id=rec.document_id, location=rec.location,
                        effective_date=rec.effective_date.isoformat() if rec.effective_date else None, origin="read_source")
    return ToolOutcome(_wrap(s, 6000), [s], summary={"record_id": rec.record_id, "chars": len(rec.content)})


TOOL_IMPLS = {
    "search_documents": tool_search_documents, "list_documents": tool_list_documents, "get_document_overview": tool_get_document_overview,
    "list_tables": tool_list_tables, "query_tables": tool_query_tables, "explore_graph": tool_explore_graph, "read_source": tool_read_source,
}

TOOL_STATUS_VI = {
    "search_documents": "Đang tìm trong tài liệu…", "list_documents": "Đang xem danh sách tài liệu…",
    "get_document_overview": "Đang đọc tổng quan tài liệu…", "list_tables": "Đang xem các bảng số liệu…",
    "query_tables": "Đang chạy truy vấn số liệu…", "explore_graph": "Đang tra đồ thị quan hệ…", "read_source": "Đang đọc chi tiết nguồn…",
}


def run_tool(tc: ToolContext, name: str, args: dict[str, Any]) -> ToolOutcome:
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return ToolOutcome(f"Tool {name} không tồn tại.", error="unknown_tool")
    with tracing.span(f"tool.{name}", input=args) as sp:
        try:
            out = impl(tc, **args)
        except TypeError as e:
            out = ToolOutcome(f"Tham số không hợp lệ cho {name}: {e}", error=str(e)[:200])
        except AppError as e:
            out = ToolOutcome(f"Lỗi: {e.message}", error=e.message[:300])
        except Exception as e:  # tool lỗi không được làm hỏng cả lượt trả lời
            logger.exception("tool %s lỗi", name)
            out = ToolOutcome(f"Lỗi hệ thống khi chạy {name}: {type(e).__name__}", error=f"{type(e).__name__}: {e}"[:300])
        sp.update(output={"sources": len(out.sources), "error": out.error, **out.summary} if out else None,
                  level="ERROR" if out.error else None)
    return out
