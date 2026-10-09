"""Số liệu "platform làm được gì với dữ liệu của bạn" cho trang chủ & trung tâm tin cậy: độ phủ tri thức (đa nguồn,
bảng, đồ thị, mô hình), chất lượng trả lời (có trích dẫn / từ chối thay vì đoán / tốc độ / kỹ năng đã dùng) và các lớp
bảo vệ đang hoạt động (duyệt câu trả lời, xung đột, nội dung nghi ngờ, phân quyền). Tất cả đo từ dữ liệu thật."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import get_db, get_services
from core.models import (ApiKey, ChunkEmbedding, ChunkRow, Conflict, DataModel, DataTable, Document, Entity, Feedback, KBRecordRow, QATrace,
                         Relation, ReviewItem, WorkspaceMember)
from core.principal import WorkspaceContext
from knowledge.translate import localize

router = APIRouter(prefix="/w/{workspace_id}", tags=["insights"])

TOOL_SKILL = {"search_documents": "documents", "read_source": "documents", "get_document_overview": "documents", "list_documents": "documents",
              "query_tables": "tables", "list_tables": "tables", "explore_graph": "graph"}


def _count(db: Session, stmt: Any) -> int:
    return int(db.execute(stmt).scalar() or 0)


@router.get("/insights", summary="Năng lực platform trên dữ liệu của workspace (tri thức, chất lượng trả lời, tin cậy)")
def insights(days: int = Query(30, ge=1, le=365), ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
             services: Any = Depends(get_services)) -> dict:
    ws = ctx.workspace_id
    cfg = services.cfg
    since = datetime.now(timezone.utc) - timedelta(days=days)

    live_docs = and_(Document.workspace_id == ws, Document.deleted_at.is_(None))
    by_type = dict(db.execute(select(Document.source_type, func.count()).where(live_docs, Document.status.in_(("active", "pending_review")))
                              .group_by(Document.source_type)).all())
    by_status = dict(db.execute(select(Document.status, func.count()).where(live_docs).group_by(Document.status)).all())
    records = _count(db, select(func.count()).select_from(KBRecordRow).where(KBRecordRow.workspace_id == ws, KBRecordRow.deleted_at.is_(None),
                                                                            KBRecordRow.is_active.is_(True)))
    chunks = _count(db, select(func.count()).select_from(ChunkRow).where(ChunkRow.workspace_id == ws))
    embedded = _count(db, select(func.count()).select_from(ChunkEmbedding).join(ChunkRow, ChunkRow.chunk_id == ChunkEmbedding.chunk_id)
                      .where(ChunkRow.workspace_id == ws))
    tables = db.execute(select(func.count(), func.coalesce(func.sum(DataTable.row_count), 0))
                        .where(DataTable.workspace_id == ws, DataTable.is_active.is_(True))).one()
    entities = _count(db, select(func.count()).select_from(Entity).where(Entity.workspace_id == ws))
    relations = _count(db, select(func.count()).select_from(Relation).where(Relation.workspace_id == ws))
    models = dict(db.execute(select(DataModel.status, func.count()).where(DataModel.workspace_id == ws, DataModel.deleted_at.is_(None))
                             .group_by(DataModel.status)).all())
    suspicious = _count(db, select(func.count()).select_from(KBRecordRow).where(
        KBRecordRow.workspace_id == ws, KBRecordRow.deleted_at.is_(None), func.jsonb_array_length(KBRecordRow.suspicious_content) > 0))
    duration = db.execute(text("SELECT COALESCE(SUM((semantic_profile->'stats'->>'duration_sec')::float), 0) FROM documents "
                               "WHERE workspace_id = :ws AND deleted_at IS NULL AND semantic_profile ? 'stats' "
                               "AND (semantic_profile->'stats') ? 'duration_sec'"), {"ws": ws}).scalar()

    # ---------------------------------------------------------------- chất lượng trả lời
    tq = and_(QATrace.workspace_id == ws, QATrace.created_at >= since)
    status_counts = dict(db.execute(select(QATrace.status, func.count()).where(tq).group_by(QATrace.status)).all())
    total_q = sum(status_counts.values())
    lat = db.execute(select(func.percentile_cont(0.5).within_group(QATrace.latency_ms), func.percentile_cont(0.95).within_group(QATrace.latency_ms),
                            func.avg(QATrace.cost_usd), func.avg(func.jsonb_array_length(QATrace.citations)))
                     .where(tq, QATrace.latency_ms.isnot(None))).one()
    tool_rows = db.execute(select(QATrace.tool_calls).where(tq).order_by(QATrace.created_at.desc()).limit(2000)).scalars().all()
    tools: dict[str, int] = {}
    skills: dict[str, int] = {"documents": 0, "tables": 0, "graph": 0}
    multi = 0
    for calls in tool_rows:
        used = {c.get("tool") for c in (calls or []) if isinstance(c, dict)}
        sk = {TOOL_SKILL.get(t or "", "other") for t in used}
        for t in used:
            if t:
                tools[t] = tools.get(t, 0) + 1
        for s_ in sk:
            if s_ in skills:
                skills[s_] += 1
        if len(sk & {"documents", "tables", "graph"}) > 1:
            multi += 1
    answered = status_counts.get("answered", 0) + status_counts.get("pending_review", 0)
    grounded_base = answered + status_counts.get("no_citation", 0) + status_counts.get("abstained", 0)
    recent = db.execute(select(QATrace.trace_id, QATrace.question, QATrace.status, QATrace.tool_calls, QATrace.citations, QATrace.latency_ms,
                               QATrace.created_at, QATrace.confidence)
                        .where(tq, QATrace.status.in_(("answered", "answered_general", "abstained", "pending_review")))
                        .order_by(QATrace.created_at.desc()).limit(6)).all()

    # ---------------------------------------------------------------- tin cậy & bảo vệ
    pending_reviews = _count(db, select(func.count()).select_from(ReviewItem).where(ReviewItem.workspace_id == ws, ReviewItem.status == "pending"))
    reviewed = _count(db, select(func.count()).select_from(ReviewItem).where(ReviewItem.workspace_id == ws, ReviewItem.status != "pending"))
    open_conflicts = _count(db, select(func.count()).select_from(Conflict).where(Conflict.workspace_id == ws, Conflict.status == "open"))
    resolved_conflicts = _count(db, select(func.count()).select_from(Conflict).where(Conflict.workspace_id == ws, Conflict.status != "open"))
    fb = dict(db.execute(select(Feedback.rating, func.count()).join(QATrace, QATrace.trace_id == Feedback.trace_id)
                         .where(QATrace.workspace_id == ws, Feedback.created_at >= since).group_by(Feedback.rating)).all())
    roles = dict(db.execute(select(WorkspaceMember.role, func.count()).where(WorkspaceMember.workspace_id == ws).group_by(WorkspaceMember.role)).all())
    keys = _count(db, select(func.count()).select_from(ApiKey).where(ApiKey.workspace_id == ws, ApiKey.active.is_(True)))

    return {
        "days": days,
        "knowledge": {
            "documents": sum(by_type.values()), "by_source_type": by_type, "by_status": by_status, "records": records, "chunks": chunks,
            "embedded_chunks": embedded, "tables": int(tables[0]), "table_rows": int(tables[1]), "entities": entities, "relations": relations,
            "models": sum(models.values()), "models_ready": models.get("ready", 0), "media_minutes": round(float(duration or 0) / 60, 1),
            "superseded": by_status.get("superseded", 0),
        },
        "answers": {
            "questions": total_q, "by_status": status_counts,
            "grounded_rate": (answered / grounded_base) if grounded_base else None,
            "abstain_rate": (status_counts.get("abstained", 0) / total_q) if total_q else None,
            "latency_p50_ms": int(lat[0]) if lat[0] is not None else None, "latency_p95_ms": int(lat[1]) if lat[1] is not None else None,
            "avg_cost_usd": float(lat[2]) if lat[2] is not None else None, "avg_citations": float(lat[3]) if lat[3] is not None else None,
            "tools": tools, "skills": skills, "multi_skill": multi,
            "recent": localize(db, ctx.workspace_id, [{"trace_id": r.trace_id, "question": r.question, "status": r.status, "latency_ms": r.latency_ms, "created_at": r.created_at,
                        "citations": len(r.citations or []), "confidence": r.confidence,
                        "skills": sorted({TOOL_SKILL.get(c.get("tool", ""), "other") for c in (r.tool_calls or []) if isinstance(c, dict)} - {"other"})}
                       for r in recent], ensure=[r.question for r in recent]),
        },
        "trust": {
            "pending_reviews": pending_reviews, "reviewed": reviewed, "open_conflicts": open_conflicts, "resolved_conflicts": resolved_conflicts,
            "feedback_up": fb.get(1, 0), "feedback_down": fb.get(-1, 0), "suspicious_records": suspicious,
            "blocked_files": by_status.get("blocked", 0), "members_by_role": roles, "api_keys": keys,
        },
        "safeguards": {
            "citations_required": bool(cfg.citation.required), "injection_shield": True, "human_review": bool(cfg.review.enabled),
            "audit_log": True, "role_based_access": True, "rate_limits": True, "read_only_sql": True,
            "abstain_when_unsure": True, "versioning": True,
        },
    }
