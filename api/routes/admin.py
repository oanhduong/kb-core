"""Vận hành: theo workspace (/w/{ws}/graph/*, /w/{ws}/ops/*) và toàn hệ thống (/admin/*, /config)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import current_principal, require_superadmin, workspace
from api.deps import audit_action, get_db, get_services
from core.errors import NotFoundError
from core.models import Abstention, Feedback, GraphAlias, QATrace
from core.principal import Principal, WorkspaceContext
from domains import get_pack, list_packs
from graph.resolve import invalidate_resolver_cache, upsert_alias
from graph.store import GraphUnavailable
from observability.costs import cost_by_role, cost_today
from observability.tracing import trace_url
from worker.queue import enqueue_job, job_view

router = APIRouter(tags=["ops"])
ws_router = APIRouter(prefix="/w/{workspace_id}", tags=["ops"])


# ------------------------------------------------------------------ graph rule-based (domain pack)
class AliasBody(BaseModel):
    kind: Literal["sku", "product", "store", "category", "supplier", "promotion", "policy"]
    alias: str = Field(min_length=1, max_length=200)
    canonical: str = Field(min_length=1, max_length=200)
    note: str | None = None


@ws_router.get("/graph/aliases", summary="Alias entity resolution (pack có graph rule-based)")
def list_aliases(kind: str | None = None, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(GraphAlias).where(GraphAlias.workspace_id == ctx.workspace_id).order_by(GraphAlias.kind, GraphAlias.alias)
    if kind:
        q = q.where(GraphAlias.kind == kind)
    return [{"id": a.id, "kind": a.kind, "alias": a.alias, "canonical": a.canonical, "note": a.note, "updated_at": a.updated_at,
             "created_by": a.created_by} for a in db.execute(q).scalars()]


@ws_router.post("/graph/aliases")
def put_alias(body: AliasBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    row = upsert_alias(db, ctx.workspace_id, body.kind, body.alias, body.canonical, note=body.note, actor=ctx.subject)
    invalidate_resolver_cache(ctx.workspace_id)
    audit_action(request, ctx.principal, "alias_upsert", str(row.id), kind=body.kind, alias=body.alias, canonical=body.canonical)
    return {"id": row.id, "kind": row.kind, "alias": row.alias, "canonical": row.canonical}


@ws_router.delete("/graph/aliases/{alias_id}")
def delete_alias(alias_id: int, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    row = db.get(GraphAlias, alias_id)
    if row is None or row.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không có alias")
    db.delete(row)
    invalidate_resolver_cache(ctx.workspace_id)
    audit_action(request, ctx.principal, "alias_delete", str(alias_id))
    return {"deleted": alias_id}


@ws_router.get("/graph/rule-stats", summary="Số node/edge graph rule-based (AGE) của workspace")
def graph_stats(ctx: WorkspaceContext = Depends(workspace("viewer")), services: Any = Depends(get_services)) -> dict:
    if not get_pack(ctx.domain_pack).rule_graph:
        return {"enabled": False, "reason": "domain pack không có graph rule-based"}
    store = services.graph_store(ctx.workspace_id)
    if store is None:
        return {"enabled": False}
    try:
        return {"enabled": True, **store.stats()}
    except GraphUnavailable as e:
        return {"enabled": True, "error": str(e)[:300]}


class RebuildBody(BaseModel):
    llm: bool = True


@ws_router.post("/graph/rebuild", summary="Rebuild toàn bộ graph rule-based của workspace (job nền)")
def graph_rebuild(body: RebuildBody, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    job = enqueue_job(db, "graph_rebuild", {"llm": body.llm}, created_by=ctx.subject, workspace_id=ctx.workspace_id)
    audit_action(request, ctx.principal, "graph_rebuild", job.id, llm=body.llm)
    return job_view(job)


# ------------------------------------------------------------------ vận hành theo workspace
@ws_router.get("/ops/abstentions", summary="Câu hỏi không trả lời được gần đây (KB đang thiếu gì)")
def abstentions(days: int = Query(7, ge=1, le=180), limit: int = Query(200, le=1000), ctx: WorkspaceContext = Depends(workspace("editor")),
                db: Session = Depends(get_db)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(Abstention).where(Abstention.workspace_id == ctx.workspace_id, Abstention.created_at >= since)
    if ctx.scope_value:
        q = q.where(Abstention.store_code == ctx.scope_value)
    return [{"trace_id": a.trace_id, "question": a.question, "reason": a.reason, "top_score": a.top_score,
             "question_type": a.question_type, "scope": a.store_code, "user_id": a.user_id, "created_at": a.created_at}
            for a in db.execute(q.order_by(Abstention.created_at.desc()).limit(limit)).scalars()]


@ws_router.get("/ops/feedback", summary="Feedback (mặc định thumbs down) kèm link trace")
def list_feedback(rating: int = -1, status: str | None = None, days: int = Query(30, le=365),
                  ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (select(Feedback, QATrace).join(QATrace, QATrace.trace_id == Feedback.trace_id)
         .where(QATrace.workspace_id == ctx.workspace_id, Feedback.rating == rating, Feedback.created_at >= since))
    if status:
        q = q.where(Feedback.status == status)
    out = []
    for fb, tr in db.execute(q.order_by(Feedback.created_at.desc()).limit(500)).all():
        if ctx.scope_value and tr.store_code not in (ctx.scope_value, None):
            continue
        out.append({"id": fb.id, "trace_id": fb.trace_id, "user_id": fb.user_id, "rating": fb.rating, "comment": fb.comment,
                    "status": fb.status, "admin_note": fb.admin_note, "created_at": fb.created_at, "question": tr.question,
                    "answer": tr.answer, "answer_status": tr.status, "scope": tr.store_code, "config_version": tr.config_version,
                    "trace_url": trace_url(tr.langfuse_trace_id)})
    return out


class FeedbackUpdate(BaseModel):
    status: Literal["new", "seen", "fixed", "wontfix"]
    admin_note: str | None = None


@ws_router.patch("/ops/feedback/{feedback_id}")
def update_feedback(feedback_id: int, body: FeedbackUpdate, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    fb = db.get(Feedback, feedback_id)
    if fb is None:
        raise NotFoundError("Không có feedback")
    tr = db.get(QATrace, fb.trace_id)
    if tr is None or tr.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không có feedback")
    fb.status, fb.admin_note = body.status, body.admin_note
    return {"id": fb.id, "status": fb.status}


@ws_router.get("/ops/traces", summary="Tìm trace theo user/session/trạng thái/câu hỏi")
def search_traces(user_id: str | None = None, session_id: str | None = None, status: str | None = None, q: str | None = None,
                  limit: int = Query(50, le=500), ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> list[dict]:
    query = select(QATrace).where(QATrace.workspace_id == ctx.workspace_id)
    for col, val in ((QATrace.user_id, user_id), (QATrace.session_id, session_id), (QATrace.status, status)):
        if val:
            query = query.where(col == val)
    if ctx.scope_value:
        query = query.where(QATrace.store_code.in_([ctx.scope_value]) | QATrace.store_code.is_(None))
    if q:
        query = query.where(QATrace.question.ilike(f"%{q}%"))
    return [{"trace_id": t.trace_id, "created_at": t.created_at, "user_id": t.user_id, "scope": t.store_code, "question": t.question,
             "status": t.status, "question_type": t.question_type, "route": t.route, "confidence": t.confidence,
             "latency_ms": t.latency_ms, "cost_usd": t.cost_usd, "config_version": t.config_version,
             "trace_url": trace_url(t.langfuse_trace_id)} for t in db.execute(query.order_by(QATrace.created_at.desc()).limit(limit)).scalars()]


@ws_router.get("/ops/traces/{trace_id}", summary="Toàn bộ dữ kiện 1 request (khi user báo trả lời sai)")
def get_trace(trace_id: str, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    t = db.get(QATrace, trace_id)
    if t is None or t.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không có trace (hoặc đã quá hạn lưu trữ)")
    return {c.name: getattr(t, c.name) for c in QATrace.__table__.columns} | {"langfuse_url": trace_url(t.langfuse_trace_id)}


@ws_router.get("/ops/costs", summary="Chi phí LLM của workspace (từ qa_traces) + toàn hệ thống hôm nay")
def ws_costs(days: int = Query(7, ge=1, le=90), ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db),
             services: Any = Depends(get_services)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import func

    rows = db.execute(select(func.date_trunc("day", QATrace.created_at), func.count(), func.sum(QATrace.cost_usd))
                      .where(QATrace.workspace_id == ctx.workspace_id, QATrace.created_at >= since)
                      .group_by(func.date_trunc("day", QATrace.created_at)).order_by(func.date_trunc("day", QATrace.created_at))).all()
    return {"days": days, "by_day": [{"day": d.date().isoformat(), "requests": int(n), "cost_usd": round(float(c or 0), 4)} for d, n, c in rows],
            "system_today_usd": round(cost_today(db), 4), "daily_budget_usd": services.cfg.alerts.daily_cost_usd}


# ------------------------------------------------------------------ toàn hệ thống
@router.get("/config", summary="Config đang chạy (version, hash, model, prompt sha256) — không có secret")
def get_config_ep(principal: Principal = Depends(current_principal), services: Any = Depends(get_services)) -> dict:
    return services.cfg.public_view()


@router.get("/domain-packs", summary="Danh sách domain pack")
def domain_packs(principal: Principal = Depends(current_principal)) -> list[dict]:
    return [{"key": p.key, "name": p.name, "description": p.description, "rule_graph": p.rule_graph,
             "review": p.review.enabled, "doc_types": list(p.doc_types),
             "member_attributes": [{"key": a.key, "label": a.label, "description": a.description} for a in p.member_attributes]}
            for p in list_packs()]


@router.get("/admin/costs", summary="Superadmin: chi phí LLM toàn hệ thống")
def costs(days: int = Query(1, ge=1, le=90), principal: Principal = Depends(require_superadmin), db: Session = Depends(get_db),
          services: Any = Depends(get_services)) -> dict:
    return {"today_usd": round(cost_today(db), 4), "daily_budget_usd": services.cfg.alerts.daily_cost_usd, "by_role": cost_by_role(db, days)}


@router.get("/admin/jobs", summary="Superadmin: hàng đợi job toàn hệ thống")
def admin_jobs(status: str | None = None, limit: int = Query(100, le=1000), principal: Principal = Depends(require_superadmin),
               db: Session = Depends(get_db)) -> list[dict]:
    from core.models import Job

    q = select(Job)
    if status:
        q = q.where(Job.status == status)
    return [job_view(j) for j in db.execute(q.order_by(Job.created_at.desc()).limit(limit)).scalars()]
