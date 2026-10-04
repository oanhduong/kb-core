"""Review queue (B6): supervisor xem câu hỏi liên quan tiền đang chờ, duyệt (có thể sửa) hoặc từ chối."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.auth import can_see_store, require_role
from api.deps import audit_action, get_db
from core.errors import ForbiddenError, NotFoundError
from core.models import ReviewItem
from core.principal import Principal
from qa import review as review_svc

router = APIRouter(prefix="/review", tags=["review"])


def _view(r: ReviewItem) -> dict:
    return {"id": r.id, "trace_id": r.trace_id, "session_id": r.session_id, "user_id": r.user_id, "store_code": r.store_code,
            "question": r.question, "draft_answer": r.draft_answer, "draft_citations": r.draft_citations,
            "decision_trace": r.decision_trace, "reason": r.reason, "status": r.status, "final_answer": r.final_answer,
            "overridden": r.overridden, "reviewer_id": r.reviewer_id, "review_note": r.review_note,
            "created_at": r.created_at, "reviewed_at": r.reviewed_at}


@router.get("", summary="Hàng đợi chờ duyệt (supervisor gắn store chỉ thấy store mình + câu hỏi không gắn store)")
def list_review(status: str = "pending", overridden: bool | None = None, limit: int = Query(100, le=500),
                principal: Principal = Depends(require_role("supervisor")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(ReviewItem).where(ReviewItem.status == status)
    if overridden is not None:
        q = q.where(ReviewItem.overridden.is_(overridden))
    if principal.store_code:
        q = q.where(or_(ReviewItem.store_code == principal.store_code, ReviewItem.store_code.is_(None)))
    return [_view(r) for r in db.execute(q.order_by(ReviewItem.created_at).limit(limit)).scalars()]


def _get_scoped(db: Session, review_id: str, principal: Principal) -> ReviewItem:
    item = db.get(ReviewItem, review_id)
    if item is None:
        raise NotFoundError(f"Không có mục review {review_id}")
    if not can_see_store(principal, item.store_code):
        raise ForbiddenError("Mục review thuộc cửa hàng khác")
    return item


class ApproveBody(BaseModel):
    edited_answer: str | None = Field(default=None, max_length=10000, description="Bỏ trống = duyệt nguyên bản nháp")
    note: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/{review_id}/approve", summary="Duyệt (có thể sửa nội dung = override, được theo dõi) rồi gửi cho user")
def approve(review_id: str, body: ApproveBody, request: Request, principal: Principal = Depends(require_role("supervisor")),
            db: Session = Depends(get_db)) -> dict:
    _get_scoped(db, review_id, principal)
    item = review_svc.approve(db, review_id, principal.subject, edited_answer=body.edited_answer, note=body.note)
    audit_action(request, principal, "review_approve", review_id, trace_id=item.trace_id, overridden=item.overridden)
    return _view(item)


@router.post("/{review_id}/reject", summary="Từ chối — user nhận thông báo liên hệ supervisor")
def reject(review_id: str, body: RejectBody, request: Request, principal: Principal = Depends(require_role("supervisor")),
           db: Session = Depends(get_db)) -> dict:
    _get_scoped(db, review_id, principal)
    item = review_svc.reject(db, review_id, principal.subject, note=body.note)
    audit_action(request, principal, "review_reject", review_id, trace_id=item.trace_id)
    return _view(item)
