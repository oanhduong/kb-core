"""Human in the loop (B6): câu hỏi liên quan TIỀN (giá, chiết khấu, hoàn tiền, bồi thường...) không trả
thẳng cho nhân viên — câu trả lời nháp (đã qua citation post-check) vào review_queue; supervisor duyệt
(giữ nguyên hoặc SỬA) hoặc từ chối, rồi mới gửi cho user (cập nhật tin nhắn chat + trace). Mọi lần
supervisor sửa nội dung = override (overridden=true, giữ cả bản nháp và bản cuối) để admin theo dõi;
mọi quyết định ghi audit log.

Vai trò trong review.bypass_roles (mặc định supervisor/admin) hỏi thì không cần duyệt.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import AppConfig
from core.errors import ConflictError, NotFoundError
from core.logging import log_event
from core.models import ChatMessage, QATrace, ReviewItem, utcnow
from graph.resolve import fold_text

logger = logging.getLogger(__name__)

# cụm chứa "giá" nhưng không phải chuyện tiền (so khớp trên chữ CÓ dấu)
_NOT_MONEY_RE = re.compile(r"đánh giá|giá trị cốt lõi|gia đình|tham gia|gia hạn|gia dụng|quốc gia")
# người dùng gõ không dấu: chỉ nhận cụm nhiều từ không mơ hồ khi bỏ dấu
_UNACCENTED_RE = re.compile(r"(?<!\w)(gia (bao nhieu|ban|niem yet|cua|la|moi|cu)|bao nhieu tien|chiet khau|giam gia|"
                            r"hoan tien|tra lai tien|boi thuong|den bu|khau tru)(?!\w)")
_AMOUNT_RE = re.compile(r"\d[\d.,]*\s*(?:k|nghìn|ngàn|triệu|tr|đ|đồng|vnđ|vnd)(?!\w)", re.IGNORECASE)


def is_money_question(question: str, cfg: AppConfig) -> tuple[bool, list[str]]:
    """Từ khoá tiền khớp trên chữ CÓ dấu (tránh "đồng" ~ "hợp đồng"/"đóng" khi bỏ dấu), cộng mẫu gõ
    không dấu nhiều từ và số tiền ("590k", "1,2 triệu")."""
    low = _NOT_MONEY_RE.sub(" ", question.lower())
    hits = []
    for kw in cfg.review.money_keywords:
        k = kw.lower().strip()
        if k == "đồng":
            pattern = r"(?<![\w])(?<!hợp )đồng(?!\w)"
        else:
            pattern = rf"(?<!\w){re.escape(k)}(?!\w)"
        if k and re.search(pattern, low):
            hits.append(kw)
    m = _UNACCENTED_RE.search(fold_text(question))
    if m and not hits:
        hits.append(m.group(1))
    if _AMOUNT_RE.search(low):
        hits.append("số tiền")
    return bool(hits), hits


def needs_review(question: str, role: str | None, cfg: AppConfig) -> tuple[bool, list[str]]:
    if not cfg.review.enabled or (role and role in cfg.review.bypass_roles):
        return False, []
    return is_money_question(question, cfg)


def enqueue_review(session: Session, *, trace_id: str, session_id: str | None, message_id: int | None,
                   user_id: str | None, store_code: str | None, question: str, draft_answer: str,
                   citations: list[dict], decision_trace: dict, reason: str) -> ReviewItem:
    existing = session.execute(select(ReviewItem).where(ReviewItem.trace_id == trace_id)).scalar_one_or_none()
    if existing is not None:
        return existing
    item = ReviewItem(trace_id=trace_id, session_id=session_id, message_id=message_id, user_id=user_id,
                      store_code=store_code, question=question, draft_answer=draft_answer,
                      draft_citations=citations, decision_trace=decision_trace, reason=reason)
    session.add(item)
    session.flush()
    log_event(logger, "review_enqueued", review_id=item.id, trace_id=trace_id, reason=reason)
    return item


def _get_pending(session: Session, review_id: str) -> ReviewItem:
    item = session.get(ReviewItem, review_id, with_for_update=True)
    if item is None:
        raise NotFoundError(f"Không có mục review {review_id}")
    if item.status != "pending":
        raise ConflictError(f"Mục review {review_id} đã được xử lý ({item.status}) bởi {item.reviewer_id}",
                            context={"status": item.status})
    return item


def _deliver(session: Session, item: ReviewItem, content: str, status: str) -> None:
    if item.message_id:
        msg = session.get(ChatMessage, item.message_id)
        if msg is not None:
            msg.content = content
            msg.status = status
            msg.payload = {**(msg.payload or {}), "review_id": item.id, "review_status": item.status,
                           "citations": item.draft_citations if status == "final" else []}
    trace = session.get(QATrace, item.trace_id)
    if trace is not None:
        trace.status = "answered" if status == "final" else "rejected"
        trace.answer = content


def approve(session: Session, review_id: str, reviewer_id: str, *, edited_answer: str | None = None,
            note: str | None = None, audit: Any = None) -> ReviewItem:
    item = _get_pending(session, review_id)
    final = (edited_answer or "").strip() or item.draft_answer
    item.status, item.final_answer, item.reviewer_id, item.review_note = "approved", final, reviewer_id, note
    item.overridden = final.strip() != item.draft_answer.strip()
    item.reviewed_at = utcnow()
    _deliver(session, item, final, "final")
    session.flush()
    if audit is not None:
        audit(action="review_approve", review_id=item.id, trace_id=item.trace_id, reviewer=reviewer_id,
              overridden=item.overridden)
    log_event(logger, "review_approved", review_id=item.id, reviewer=reviewer_id, overridden=item.overridden)
    return item


def reject(session: Session, review_id: str, reviewer_id: str, *, note: str | None = None, audit: Any = None) -> ReviewItem:
    item = _get_pending(session, review_id)
    item.status, item.reviewer_id, item.review_note, item.reviewed_at = "rejected", reviewer_id, note, utcnow()
    message = ("Supervisor chưa xác nhận câu trả lời cho câu hỏi này"
               + (f": {note}" if note else "") + ". Vui lòng liên hệ trực tiếp supervisor/quản lý ca.")
    item.final_answer = message
    _deliver(session, item, message, "rejected")
    session.flush()
    if audit is not None:
        audit(action="review_reject", review_id=item.id, trace_id=item.trace_id, reviewer=reviewer_id)
    log_event(logger, "review_rejected", review_id=item.id, reviewer=reviewer_id)
    return item
