"""QA endpoints: POST /chat (SSE), POST /ask (one-shot), POST /feedback, session + trạng thái câu trả lời."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from api.auth import current_principal, effective_store
from api.deps import get_db, get_services
from api.sse import sse_response
from core.errors import ForbiddenError, NotFoundError
from core.models import ChatSession, Feedback, QATrace
from core.principal import Principal
from observability.tracing import trace_url
from qa.answer import QARequest, QAResponse

router = APIRouter(tags=["qa"])


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    store_code: str | None = Field(default=None, max_length=32)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("câu hỏi không được rỗng")
        return v


class AskBody(ChatBody):
    session_id: None = None
    as_of: date | None = Field(default=None, description="Hỏi dữ liệu có hiệu lực tại ngày này (lịch sử giá...)")


@router.post("/chat", summary="Chat nhiều lượt — stream SSE: meta, delta*, [replace], citations, conflicts, trace, done")
def chat(body: ChatBody, request: Request, principal: Principal = Depends(current_principal),
         services: Any = Depends(get_services)):
    store = effective_store(principal, body.store_code)
    req = QARequest(question=body.question, principal=principal, store_code=store, channel="chat", session_id=body.session_id)
    return sse_response(lambda: services.qa.stream(req))


@router.post("/ask", response_model=QAResponse, summary="Hỏi one-shot, không session")
def ask(body: AskBody, principal: Principal = Depends(current_principal), services: Any = Depends(get_services)) -> QAResponse:
    store = effective_store(principal, body.store_code)
    return services.qa.answer(QARequest(question=body.question, principal=principal, store_code=store, channel="ask",
                                        as_of=body.as_of))


class FeedbackBody(BaseModel):
    trace_id: str = Field(min_length=8, max_length=32)
    rating: Literal["up", "down", 1, -1]
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback", summary="Thumbs up/down + comment gắn vào trace_id (idempotent theo trace + user)")
def feedback(body: FeedbackBody, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    trace = db.get(QATrace, body.trace_id)
    if trace is None:
        raise NotFoundError("Không tìm thấy câu trả lời (trace_id) — có thể đã quá hạn lưu trữ.")
    if trace.user_id != principal.subject and not principal.has_role("supervisor"):
        raise ForbiddenError("Chỉ phản hồi được câu trả lời của chính bạn.")
    rating = 1 if body.rating in ("up", 1) else -1
    db.execute(insert(Feedback).values(trace_id=body.trace_id, user_id=principal.subject, rating=rating, comment=body.comment)
               .on_conflict_do_update(constraint="uq_feedback_trace_user",
                                      set_={"rating": rating, "comment": body.comment, "status": "new"}))
    return {"ok": True, "trace_id": body.trace_id, "rating": rating}


@router.get("/chat/sessions", summary="Danh sách session của user hiện tại")
def list_sessions(principal: Principal = Depends(current_principal), db: Session = Depends(get_db),
                  limit: int = Query(20, le=100)) -> list[dict]:
    rows = db.execute(select(ChatSession).where(ChatSession.user_id == principal.subject)
                      .order_by(ChatSession.updated_at.desc()).limit(limit)).scalars().all()
    return [{"session_id": r.id, "title": r.title, "store_code": r.store_code, "updated_at": r.updated_at} for r in rows]


@router.get("/chat/sessions/{session_id}/messages", summary="Tin nhắn của session (after_id để poll câu trả lời chờ duyệt)")
def session_messages(session_id: str, after_id: int | None = None, principal: Principal = Depends(current_principal),
                     db: Session = Depends(get_db), services: Any = Depends(get_services)) -> list[dict]:
    sess = services.qa.sessions.get_owned(db, session_id, principal.subject, allow_any=principal.has_role("admin"))
    return [{"id": m.id, "role": m.role, "content": m.content, "status": m.status, "trace_id": m.trace_id,
             "citations": (m.payload or {}).get("citations", []), "created_at": m.created_at}
            for m in services.qa.sessions.list_messages(db, sess, after_id)]


@router.get("/answers/{trace_id}", summary="Trạng thái/nội dung 1 câu trả lời (poll khi đang chờ supervisor duyệt)")
def get_answer(trace_id: str, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    t = db.get(QATrace, trace_id)
    if t is None:
        raise NotFoundError("Không tìm thấy trace")
    if t.user_id != principal.subject and not principal.has_role("supervisor"):
        raise ForbiddenError("Không có quyền xem câu trả lời này")
    pending = t.status == "pending_review"
    return {"trace_id": t.trace_id, "status": t.status, "question": t.question,
            "answer": None if pending else t.answer, "citations": [] if pending else t.citations,
            "conflicts": t.conflicts, "decision_trace": t.decision_trace if principal.has_role("supervisor") or not pending else {},
            "created_at": t.created_at, "trace_url": trace_url(t.langfuse_trace_id)}
