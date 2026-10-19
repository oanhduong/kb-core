"""Sinh báo cáo (job report_generate): prompt người dùng -> dàn ý (LLM, prompt report_plan) -> từng mục chạy
assistant agent (tool thật: tìm văn bản, SQL, đồ thị) -> block Markdown có citation + chart/table artifact
-> reports.blocks. Người dùng sửa/sắp xếp block trên UI, ghim thêm kết quả từ chat; xuất PDF/DOCX/XLSX
(reports/export.py).

Block schema (JSONB `reports.blocks`):
    {"id", "type": "heading|markdown|chart|table|kpi|sources", "title"?, "text"? (markdown), "chart"? (vega_lite...),
     "columns"/"rows"? (table), "citations"?: [...], "trace_id"?, "sql"?}
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig, read_prompt
from core.db import session_scope
from core.i18n import L, language_name
from core.logging import log_event
from core.models import DataTable, Document, Entity, Report, Workspace, new_id, utcnow
from core.principal import Principal, WorkspaceContext

logger = logging.getLogger(__name__)


class _Section(BaseModel):
    heading: str
    goal: str = ""
    kind: str = "narrative"
    question: str


class ReportPlan(BaseModel):
    title: str
    sections: list[_Section] = Field(min_length=1, max_length=10)


def _workspace_brief(session, workspace_id: str) -> str:
    docs = session.execute(select(Document).where(Document.workspace_id == workspace_id, Document.is_active.is_(True),
                                                  Document.deleted_at.is_(None)).order_by(Document.created_at.desc()).limit(40)).scalars().all()
    tables = session.execute(select(DataTable).where(DataTable.workspace_id == workspace_id, DataTable.is_active.is_(True))).scalars().all()
    ents = session.execute(select(Entity.name, Entity.type, Entity.mention_count).where(Entity.workspace_id == workspace_id)
                           .order_by(Entity.mention_count.desc()).limit(25)).all()
    lines = ["Tài liệu:"] + [f"- {d.title} ({d.doc_type}" + (f", hiệu lực {d.effective_date}" if d.effective_date else "") + ")"
                             + (f": {(d.summary or '')[:160]}" if d.summary else "") for d in docs]
    lines += ["Bảng số liệu:"] + [f"- {t.name}: {t.title}, {t.row_count} dòng, cột {', '.join(c['name'] for c in (t.columns or [])[:10])}" for t in tables]
    lines += ["Thực thể nổi bật: " + ", ".join(f"{n} ({t})" for n, t, _ in ents)]
    return "\n".join(lines)


def plan_report(cfg: AppConfig, llm: Any, prompt: str, brief: str) -> ReportPlan:
    user = (f"Yêu cầu của người dùng: {prompt}\n\nWorkspace đang có:\n{brief}\n\nHôm nay: {date.today().isoformat()}"
            f"\n\nViết tiêu đề báo cáo, tiêu đề và mục tiêu từng mục bằng {language_name()}.")
    return llm.complete_json("report", system=read_prompt("report_plan", cfg), user=user, schema=ReportPlan, max_tokens=1500, max_retries=1)


def blocks_from_answer(section: _Section, resp: Any) -> list[dict[str, Any]]:
    """AssistantResponse -> block: heading + markdown (có [n]) + chart/table từ artifact + sources."""
    blocks: list[dict[str, Any]] = [{"id": new_id(), "type": "heading", "text": section.heading, "level": 2}]
    if resp.status in ("answered", "answered_general"):
        blocks.append({"id": new_id(), "type": "markdown", "text": resp.answer, "citations": resp.citations, "trace_id": resp.trace_id})
    elif resp.status == "pending_review":
        blocks.append({"id": new_id(), "type": "markdown", "text": L("_Nội dung mục này đang chờ người phụ trách duyệt._", "_This section is waiting for approval by the person in charge._"), "trace_id": resp.trace_id})
    else:
        blocks.append({"id": new_id(), "type": "markdown", "text": L(f"_Không đủ dữ liệu trong workspace để trả lời: {section.question}_", f"_Not enough data in the workspace to answer: {section.question}_"), "trace_id": resp.trace_id})
    for art in resp.artifacts:
        if art.get("type") != "query_result":
            continue
        chart = art.get("chart")
        if chart and chart.get("vega_lite"):
            blocks.append({"id": new_id(), "type": "chart", "title": chart.get("title") or art.get("purpose") or "", "chart": chart,
                           "sql": art.get("sql"), "citation": art.get("citation"), "trace_id": resp.trace_id})
        elif chart and chart.get("type") == "kpi":
            blocks.append({"id": new_id(), "type": "kpi", "title": chart.get("title") or art.get("purpose") or "", "kpi": chart["kpi"], "sql": art.get("sql")})
        if art.get("rows") and (not chart or chart.get("type") in ("table", "kpi") or len(art["rows"]) <= 30):
            blocks.append({"id": new_id(), "type": "table", "title": art.get("purpose") or L("Số liệu", "Figures"), "columns": art["columns"],
                           "rows": art["rows"][:200], "sql": art.get("sql"), "citation": art.get("citation"), "trace_id": resp.trace_id})
    return blocks


def generate_report(cfg: AppConfig, sm: sessionmaker, llm: Any, report_id: str, progress: Callable[..., None] | None = None) -> dict[str, Any]:
    progress = progress or (lambda *a, **k: None)
    with session_scope() as s:
        rep = s.get(Report, report_id)
        if rep is None or rep.deleted_at is not None:
            return {"skipped": "không có báo cáo"}
        rep.status = "generating"
        ws = s.get(Workspace, rep.workspace_id)
        brief = _workspace_brief(s, rep.workspace_id)
        prompt, ws_id, pack, created_by, ws_name = rep.prompt or rep.title, rep.workspace_id, ws.domain_pack, rep.created_by, ws.name
    if llm is None:
        with session_scope() as s:
            rep = s.get(Report, report_id)
            rep.status, rep.blocks = "error", [{"id": new_id(), "type": "markdown", "text": L("Thiếu ANTHROPIC_API_KEY — không sinh được báo cáo.", "ANTHROPIC_API_KEY is missing, so the report can't be generated.")}]
        return {"error": "no_llm"}
    try:
        plan = plan_report(cfg, llm, prompt, brief)
    except Exception as e:
        msg = getattr(e, "message", None) or str(e)  # KBError: chỉ phần cho người dùng, không kèm context kỹ thuật
        with session_scope() as s:
            rep = s.get(Report, report_id)
            rep.status = "error"
            rep.blocks = [{"id": new_id(), "type": "markdown", "text": L(f"Không lập được dàn ý: {msg}", f"Couldn't draft the outline: {msg}")[:500]}]
        return {"error": str(e)[:300]}

    from assistant.agent import AssistantRequest, AssistantService
    from index.bm25_cache import BM25Cache
    from index.embeddings import get_embedder
    from index.kb_store import KBVectorStore
    from qa.retrieval import VectorRetriever

    emb = get_embedder(cfg)
    retriever = VectorRetriever(cfg, KBVectorStore(sm, emb.model_name, emb.dim), BM25Cache(sm, 0).get, emb)
    if cfg.retrieval.rerank.enabled:
        try:
            retriever.rerank_fn = VectorRetriever.default_rerank_fn(cfg)
            retriever._rerank_ready.set()  # noqa: SLF001
        except Exception as e:  # không có model rerank trong worker -> hybrid
            log_event(logger, "report_rerank_unavailable", level=logging.WARNING, error=str(e)[:200])
    assistant = AssistantService(cfg, sm, llm, retriever)
    ctx = WorkspaceContext(ws_id, ws_name, pack, "owner", {}, {}, Principal(subject=created_by))
    blocks: list[dict[str, Any]] = [{"id": new_id(), "type": "heading", "text": plan.title, "level": 1},
                                    {"id": new_id(), "type": "markdown", "text": L(f"_Báo cáo sinh tự động từ dữ liệu workspace ngày {date.today():%d/%m/%Y}. Mọi số liệu/nhận định đều có trích dẫn nguồn._", f"_Report generated automatically from the workspace data on {date.today():%d %b %Y}. Every figure and statement cites its source._")}]
    traces = []
    total = len(plan.sections)
    for i, sec in enumerate(plan.sections):
        progress(i, total, sec.heading[:60])
        resp = assistant.answer(AssistantRequest(question=f"{sec.question}\n(Mục báo cáo: {sec.heading}. Mục tiêu: {sec.goal}. Viết ngắn gọn, có số liệu/trích dẫn.)",
                                                 ctx=ctx, channel="ask"))
        traces.append(resp.trace_id)
        blocks += blocks_from_answer(sec, resp)
    all_cits = {c["label"]: c for b in blocks for c in (b.get("citations") or [])}
    blocks.append({"id": new_id(), "type": "sources", "title": L("Nguồn tham chiếu", "Sources"), "items": list(all_cits.values())})
    with session_scope() as s:
        rep = s.get(Report, report_id)
        if not rep.title or rep.title in (rep.prompt, (rep.prompt or "")[:120]):  # tiêu đề mặc định lấy từ prompt -> thay bằng tiêu đề AI đặt
            rep.title = plan.title
        rep.blocks, rep.status, rep.updated_at = blocks, "ready", utcnow()
    progress(total, total, "done")
    log_event(logger, "report_generated", report_id=report_id, sections=total, blocks=len(blocks))
    return {"sections": total, "blocks": len(blocks), "traces": traces}
