"""Admin/vận hành: phát JWT, API key, alias entity (A3), graph (stats/rebuild), abstention gần đây,
feedback thumbs-down (E4), chi phí LLM, config đang chạy (F1), tra trace khi user báo lỗi."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import create_api_key, issue_jwt, require_role
from api.deps import audit_action, get_db, get_services
from core.errors import ForbiddenError, NotFoundError
from core.models import Abstention, ApiKey, Feedback, GraphAlias, QATrace
from core.principal import Principal
from graph.resolve import invalidate_resolver_cache, upsert_alias
from graph.store import GraphUnavailable
from observability.costs import cost_by_role, cost_today
from observability.tracing import trace_url
from worker.queue import enqueue_job, job_view

router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ auth
class TokenBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    role: Literal["staff", "supervisor", "admin"] = "staff"
    store_code: str | None = None
    ttl_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 30)


@router.post("/auth/token", summary="Phát JWT cho user (gọi bởi hệ thống đăng nhập của khách bằng API key admin)")
def auth_token(body: TokenBody, request: Request, principal: Principal = Depends(require_role("admin"))) -> dict:
    token = issue_jwt(user_id=body.user_id, role=body.role, store_code=body.store_code, ttl_minutes=body.ttl_minutes)
    audit_action(request, principal, "issue_jwt", body.user_id, role=body.role, store_code=body.store_code)
    return {"access_token": token, "token_type": "bearer"}


class DevLoginBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=60)
    store_code: str | None = "GM-HN01"
    role: Literal["staff", "supervisor"] = "staff"


@router.post("/auth/dev-login", summary="CHỈ môi trường dev (APP_ENV=dev, DEV_LOGIN_ENABLED=true): tự phát JWT để thử chat UI")
def dev_login(body: DevLoginBody, services: Any = Depends(get_services)) -> dict:
    if services.settings.app_env != "dev" or os.environ.get("DEV_LOGIN_ENABLED", "false").lower() != "true":
        raise ForbiddenError("dev-login bị tắt ngoài môi trường dev")
    return {"access_token": issue_jwt(user_id=body.user_id, role=body.role, store_code=body.store_code, ttl_minutes=480),
            "token_type": "bearer", "user_id": body.user_id, "role": body.role, "store_code": body.store_code}


@router.get("/auth/me", summary="Thông tin tài khoản hiện tại")
def me(principal: Principal = Depends(require_role("staff"))) -> dict:
    return {"subject": principal.subject, "role": principal.role, "store_code": principal.store_code, "kind": principal.kind}


class ApiKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal["staff", "supervisor", "admin"] = "staff"
    store_code: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)


@router.post("/admin/api-keys", summary="Tạo API key service-to-service (key thô chỉ hiện 1 lần)")
def create_key(body: ApiKeyBody, request: Request, principal: Principal = Depends(require_role("admin")),
               db: Session = Depends(get_db)) -> dict:
    row, raw = create_api_key(db, name=body.name, role=body.role, store_code=body.store_code,
                              rate_limit=body.rate_limit_per_minute, created_by=principal.subject)
    audit_action(request, principal, "api_key_create", row.id, name=body.name, role=body.role)
    return {"id": row.id, "name": row.name, "api_key": raw, "role": row.role, "store_code": row.store_code,
            "rate_limit_per_minute": row.rate_limit_per_minute}


@router.get("/admin/api-keys")
def list_keys(principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": k.id, "name": k.name, "prefix": k.key_prefix, "role": k.role, "store_code": k.store_code,
             "rate_limit_per_minute": k.rate_limit_per_minute, "active": k.active, "last_used_at": k.last_used_at}
            for k in db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars()]


@router.delete("/admin/api-keys/{key_id}", summary="Thu hồi API key")
def revoke_key(key_id: str, request: Request, principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    k = db.get(ApiKey, key_id)
    if k is None:
        raise NotFoundError("Không có API key")
    k.active = False
    audit_action(request, principal, "api_key_revoke", key_id)
    return {"id": key_id, "active": False}


# ------------------------------------------------------------------ alias (A3) + graph
class AliasBody(BaseModel):
    kind: Literal["sku", "product", "store", "category", "supplier", "promotion", "policy"]
    alias: str = Field(min_length=1, max_length=200)
    canonical: str = Field(min_length=1, max_length=200)
    note: str | None = None


@router.get("/graph/aliases", summary="Alias table cho entity resolution (admin sửa được)")
def list_aliases(kind: str | None = None, principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(GraphAlias).order_by(GraphAlias.kind, GraphAlias.alias)
    if kind:
        q = q.where(GraphAlias.kind == kind)
    return [{"id": a.id, "kind": a.kind, "alias": a.alias, "canonical": a.canonical, "note": a.note,
             "updated_at": a.updated_at, "created_by": a.created_by} for a in db.execute(q).scalars()]


@router.post("/graph/aliases", summary="Thêm/sửa alias (idempotent theo kind + alias chuẩn hoá)")
def put_alias(body: AliasBody, request: Request, principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    row = upsert_alias(db, body.kind, body.alias, body.canonical, note=body.note, actor=principal.subject)
    invalidate_resolver_cache()
    audit_action(request, principal, "alias_upsert", str(row.id), kind=body.kind, alias=body.alias, canonical=body.canonical)
    return {"id": row.id, "kind": row.kind, "alias": row.alias, "canonical": row.canonical,
            "hint": "Alias tên sản phẩm (product) áp dụng khi build lại graph: POST /graph/rebuild hoặc sync file liên quan."}


@router.delete("/graph/aliases/{alias_id}")
def delete_alias(alias_id: int, request: Request, principal: Principal = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    row = db.get(GraphAlias, alias_id)
    if row is None:
        raise NotFoundError("Không có alias")
    db.delete(row)
    invalidate_resolver_cache()
    audit_action(request, principal, "alias_delete", str(alias_id))
    return {"deleted": alias_id}


@router.get("/graph/stats", summary="Số node/edge theo loại")
def graph_stats(principal: Principal = Depends(require_role("supervisor")), services: Any = Depends(get_services)) -> dict:
    store = services.graph_store()
    if store is None:
        return {"enabled": False}
    try:
        return store.stats()
    except GraphUnavailable as e:
        return {"enabled": True, "error": str(e)[:300]}


class RebuildBody(BaseModel):
    llm: bool = True


@router.post("/graph/rebuild", summary="Rebuild TOÀN BỘ graph (job nền) — lệnh riêng, dùng khi đổi logic builder/graph hỏng")
def graph_rebuild(body: RebuildBody, request: Request, principal: Principal = Depends(require_role("admin")),
                  db: Session = Depends(get_db)) -> dict:
    job = enqueue_job(db, "graph_rebuild", {"llm": body.llm}, created_by=principal.subject)
    audit_action(request, principal, "graph_rebuild", job.id, llm=body.llm)
    return job_view(job)


# ------------------------------------------------------------------ vận hành
@router.get("/admin/abstentions", summary="Câu hỏi bị abstention gần đây (KB đang thiếu gì)")
def abstentions(days: int = Query(7, ge=1, le=180), limit: int = Query(200, le=1000),
                principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(Abstention).where(Abstention.created_at >= since)
    if principal.store_code:
        q = q.where(Abstention.store_code == principal.store_code)
    return [{"trace_id": a.trace_id, "question": a.question, "reason": a.reason, "top_score": a.top_score,
             "question_type": a.question_type, "store_code": a.store_code, "user_id": a.user_id, "created_at": a.created_at}
            for a in db.execute(q.order_by(Abstention.created_at.desc()).limit(limit)).scalars()]


@router.get("/admin/feedback", summary="Feedback (mặc định thumbs down) kèm link trace — để admin xem, không dùng làm dataset")
def list_feedback(rating: int = -1, status: str | None = None, days: int = Query(30, le=365),
                  principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db),
                  services: Any = Depends(get_services)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(Feedback, QATrace).join(QATrace, QATrace.trace_id == Feedback.trace_id, isouter=True) \
        .where(Feedback.rating == rating, Feedback.created_at >= since)
    if status:
        q = q.where(Feedback.status == status)
    out = []
    for fb, tr in db.execute(q.order_by(Feedback.created_at.desc()).limit(500)).all():
        if principal.store_code and tr is not None and tr.store_code not in (principal.store_code, None):
            continue
        out.append({"id": fb.id, "trace_id": fb.trace_id, "user_id": fb.user_id, "rating": fb.rating, "comment": fb.comment,
                    "status": fb.status, "admin_note": fb.admin_note, "created_at": fb.created_at,
                    "question": tr.question if tr else None, "answer": tr.answer if tr else None,
                    "answer_status": tr.status if tr else "trace đã hết hạn lưu trữ",
                    "store_code": tr.store_code if tr else None, "config_version": tr.config_version if tr else None,
                    "trace_url": trace_url(tr.langfuse_trace_id) if tr else None,
                    "api_trace": f"/admin/traces/{fb.trace_id}"})
    return out


class FeedbackUpdate(BaseModel):
    status: Literal["new", "seen", "fixed", "wontfix"]
    admin_note: str | None = None


@router.patch("/admin/feedback/{feedback_id}")
def update_feedback(feedback_id: int, body: FeedbackUpdate, principal: Principal = Depends(require_role("admin")),
                    db: Session = Depends(get_db)) -> dict:
    fb = db.get(Feedback, feedback_id)
    if fb is None:
        raise NotFoundError("Không có feedback")
    fb.status, fb.admin_note = body.status, body.admin_note
    return {"id": fb.id, "status": fb.status}


@router.get("/admin/traces/{trace_id}", summary="Toàn bộ dữ kiện 1 request (khi user báo trả lời sai)")
def get_trace(trace_id: str, principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> dict:
    t = db.get(QATrace, trace_id)
    if t is None:
        raise NotFoundError("Không có trace (hoặc đã quá hạn lưu trữ 30 ngày)")
    if principal.store_code and t.store_code not in (principal.store_code, None):
        raise ForbiddenError("Trace thuộc cửa hàng khác")
    return {c.name: getattr(t, c.name) for c in QATrace.__table__.columns} | {"langfuse_url": trace_url(t.langfuse_trace_id)}


@router.get("/admin/traces", summary="Tìm trace theo user/session/store/trạng thái")
def search_traces(user_id: str | None = None, session_id: str | None = None, store_code: str | None = None,
                  status: str | None = None, q: str | None = None, limit: int = Query(50, le=500),
                  principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> list[dict]:
    query = select(QATrace)
    for col, val in ((QATrace.user_id, user_id), (QATrace.session_id, session_id), (QATrace.status, status)):
        if val:
            query = query.where(col == val)
    store = principal.store_code or store_code
    if store:
        query = query.where(QATrace.store_code == store)
    if q:
        query = query.where(QATrace.question.ilike(f"%{q}%"))
    return [{"trace_id": t.trace_id, "created_at": t.created_at, "user_id": t.user_id, "store_code": t.store_code,
             "question": t.question, "status": t.status, "question_type": t.question_type, "route": t.route,
             "confidence": t.confidence, "latency_ms": t.latency_ms, "cost_usd": t.cost_usd,
             "config_version": t.config_version, "trace_url": trace_url(t.langfuse_trace_id)}
            for t in db.execute(query.order_by(QATrace.created_at.desc()).limit(limit)).scalars()]


@router.get("/admin/costs", summary="Chi phí LLM hôm nay + theo vai trò/model")
def costs(days: int = Query(1, ge=1, le=90), principal: Principal = Depends(require_role("admin")),
          db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    return {"today_usd": round(cost_today(db), 4), "daily_budget_usd": services.cfg.alerts.daily_cost_usd,
            "by_role": cost_by_role(db, days)}


@router.get("/config", summary="Config đang chạy: version, hash, model, tham số, prompt + sha256 (không có secret)")
def get_config_ep(principal: Principal = Depends(require_role("supervisor")), services: Any = Depends(get_services)) -> dict:
    return services.cfg.public_view()
