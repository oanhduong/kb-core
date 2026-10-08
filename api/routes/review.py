"""Review queue theo workspace (human-in-the-loop của domain pack): editor/owner duyệt, sửa, từ chối."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db
from core.errors import ForbiddenError, NotFoundError
from core.i18n import L
from core.models import ReviewItem
from core.principal import WorkspaceContext
from qa import review as review_svc

router = APIRouter(prefix="/w/{workspace_id}/review", tags=["review"])


def _view(r: ReviewItem) -> dict:
    return {"id": r.id, "trace_id": r.trace_id, "session_id": r.session_id, "user_id": r.user_id, "scope": r.store_code,
            "question": r.question, "draft_answer": r.draft_answer, "draft_citations": r.draft_citations,
            "decision_trace": r.decision_trace, "reason": r.reason, "status": r.status, "final_answer": r.final_answer,
            "overridden": r.overridden, "reviewer_id": r.reviewer_id, "review_note": r.review_note,
            "created_at": r.created_at, "reviewed_at": r.reviewed_at}


@router.get("", summary="Hàng đợi chờ duyệt (thành viên có scope chỉ thấy scope mình + câu hỏi không gắn scope)")
def list_review(status: str = "pending", overridden: bool | None = None, limit: int = Query(100, le=500),
                ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(ReviewItem).where(ReviewItem.workspace_id == ctx.workspace_id, ReviewItem.status == status)
    if overridden is not None:
        q = q.where(ReviewItem.overridden.is_(overridden))
    if ctx.scope_value:
        q = q.where(or_(ReviewItem.store_code == ctx.scope_value, ReviewItem.store_code.is_(None)))
    return [_view(r) for r in db.execute(q.order_by(ReviewItem.created_at).limit(limit)).scalars()]


def _get_scoped(db: Session, review_id: str, ctx: WorkspaceContext) -> ReviewItem:
    item = db.get(ReviewItem, review_id)
    if item is None or item.workspace_id != ctx.workspace_id:
        raise NotFoundError(L(f"Không có mục review {review_id}", f"Review item {review_id} not found"))
    if ctx.scope_value and item.store_code not in (ctx.scope_value, None):
        raise ForbiddenError(L("Mục review thuộc phạm vi khác", "This review item belongs to a different scope"))
    return item


class ApproveBody(BaseModel):
    edited_answer: str | None = Field(default=None, max_length=10000, description="Bỏ trống = duyệt nguyên bản nháp")
    note: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/{review_id}/approve", summary="Duyệt (có thể sửa = override, được theo dõi) rồi gửi cho người hỏi")
def approve(review_id: str, body: ApproveBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
            db: Session = Depends(get_db)) -> dict:
    _get_scoped(db, review_id, ctx)
    item = review_svc.approve(db, review_id, ctx.subject, edited_answer=body.edited_answer, note=body.note)
    audit_action(request, ctx.principal, "review_approve", review_id, trace_id=item.trace_id, overridden=item.overridden)
    return _view(item)


@router.post("/{review_id}/reject", summary="Từ chối — người hỏi nhận thông báo liên hệ người phụ trách")
def reject(review_id: str, body: RejectBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
           db: Session = Depends(get_db)) -> dict:
    _get_scoped(db, review_id, ctx)
    item = review_svc.reject(db, review_id, ctx.subject, note=body.note)
    audit_action(request, ctx.principal, "review_reject", review_id, trace_id=item.trace_id)
    return _view(item)
