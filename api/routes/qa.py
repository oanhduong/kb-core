"""QA theo workspace: POST /w/{ws}/chat (SSE), /w/{ws}/ask, /w/{ws}/feedback, session, trạng thái câu trả lời."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from api.auth import effective_scope, workspace
from api.deps import get_db, get_services
from api.sse import sse_response
from core.errors import ForbiddenError, NotFoundError
from core.models import ChatSession, Feedback, QATrace
from core.principal import WorkspaceContext
from observability.tracing import trace_url
from assistant.agent import AssistantRequest, AssistantResponse
from qa.answer import QARequest, QAResponse

router = APIRouter(prefix="/w/{workspace_id}", tags=["qa"])


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    scope: str | None = Field(default=None, max_length=32, description="Pack retail: mã cửa hàng")
    document_ids: list[str] | None = Field(default=None, max_length=50, description="Chỉ hỏi trong các tài liệu này")
    # agent (mặc định): trợ lý tổng quát dùng tool (tìm KB, bảng số liệu, đồ thị, tóm tắt) — strict: pipeline hỏi đáp
    # cổ điển (router -> hybrid/graph rule-based -> trả lời có citation), nhanh/rẻ hơn cho tra cứu thuần tuý
    mode: Literal["agent", "strict"] = "agent"

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("câu hỏi không được rỗng")
        return v


class AskBody(ChatBody):
    session_id: None = None
    as_of: date | None = Field(default=None, description="Hỏi dữ liệu có hiệu lực tại ngày này")


def _request(body: ChatBody, ctx: WorkspaceContext, channel: str, **extra: Any) -> QARequest:
    return QARequest(question=body.question, ctx=ctx, store_code=effective_scope(ctx, body.scope), channel=channel,
                     session_id=getattr(body, "session_id", None), document_ids=body.document_ids, **extra)


def _agent_request(body: ChatBody, ctx: WorkspaceContext, channel: str, **extra: Any) -> AssistantRequest:
    return AssistantRequest(question=body.question, ctx=ctx, scope=effective_scope(ctx, body.scope), channel=channel,
                            session_id=getattr(body, "session_id", None), document_ids=body.document_ids, **extra)


@router.post("/chat", summary="Chat nhiều lượt — SSE: meta, (status|artifact|delta)*, [replace], [pending_review], citations, [conflicts], trace, done")
def chat(body: ChatBody, ctx: WorkspaceContext = Depends(workspace("viewer")), services: Any = Depends(get_services)):
    if body.mode == "strict":
        req = _request(body, ctx, "chat")
        return sse_response(lambda: services.qa.stream(req))
    areq = _agent_request(body, ctx, "chat")
    return sse_response(lambda: services.assistant.stream(areq))


@router.post("/ask", summary="Hỏi one-shot (không session). mode=agent (mặc định) | strict", response_model=None)
def ask(body: AskBody, ctx: WorkspaceContext = Depends(workspace("viewer")), services: Any = Depends(get_services)) -> QAResponse | AssistantResponse:
    if body.mode == "strict":
        return services.qa.answer(_request(body, ctx, "ask", as_of=body.as_of))
    return services.assistant.answer(_agent_request(body, ctx, "ask", as_of=body.as_of))


class FeedbackBody(BaseModel):
    trace_id: str = Field(min_length=8, max_length=32)
    rating: Literal["up", "down", 1, -1]
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback", summary="Thumbs up/down + comment gắn trace_id (idempotent theo trace + user)")
def feedback(body: FeedbackBody, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    trace = db.get(QATrace, body.trace_id)
    if trace is None or trace.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không tìm thấy câu trả lời (trace_id) — có thể đã quá hạn lưu trữ.")
    if trace.user_id != ctx.subject and not ctx.has_role("editor"):
        raise ForbiddenError("Chỉ phản hồi được câu trả lời của chính bạn.")
    rating = 1 if body.rating in ("up", 1) else -1
    db.execute(insert(Feedback).values(trace_id=body.trace_id, user_id=ctx.subject, rating=rating, comment=body.comment)
               .on_conflict_do_update(constraint="uq_feedback_trace_user", set_={"rating": rating, "comment": body.comment, "status": "new"}))
    return {"ok": True, "trace_id": body.trace_id, "rating": rating}


@router.get("/chat/sessions", summary="Session chat của tôi trong workspace")
def list_sessions(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
                  limit: int = Query(30, le=200)) -> list[dict]:
    rows = db.execute(select(ChatSession).where(ChatSession.workspace_id == ctx.workspace_id, ChatSession.user_id == ctx.subject)
                      .order_by(ChatSession.updated_at.desc()).limit(limit)).scalars().all()
    return [{"session_id": r.id, "title": r.title, "scope": r.store_code, "updated_at": r.updated_at, "created_at": r.created_at} for r in rows]


@router.get("/chat/sessions/{session_id}/messages", summary="Tin nhắn của session (after_id để poll)")
def session_messages(session_id: str, after_id: int | None = None, ctx: WorkspaceContext = Depends(workspace("viewer")),
                     db: Session = Depends(get_db), services: Any = Depends(get_services)) -> list[dict]:
    sess = services.qa.sessions.get_owned(db, session_id, ctx.subject, workspace_id=ctx.workspace_id, allow_any=ctx.has_role("owner"))
    return [{"id": m.id, "role": m.role, "content": m.content, "status": m.status, "trace_id": m.trace_id,
             "citations": (m.payload or {}).get("citations", []), "artifacts": (m.payload or {}).get("artifacts", []),
             "created_at": m.created_at} for m in services.qa.sessions.list_messages(db, sess, after_id)]


@router.delete("/chat/sessions/{session_id}", summary="Xoá session chat của tôi")
def delete_session(session_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
                   services: Any = Depends(get_services)) -> dict:
    sess = services.qa.sessions.get_owned(db, session_id, ctx.subject, workspace_id=ctx.workspace_id)
    db.delete(sess)
    return {"deleted": session_id}


@router.get("/answers/{trace_id}", summary="Trạng thái/nội dung 1 câu trả lời (poll khi chờ duyệt)")
def get_answer(trace_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    t = db.get(QATrace, trace_id)
    if t is None or t.workspace_id != ctx.workspace_id:
        raise NotFoundError("Không tìm thấy trace")
    if t.user_id != ctx.subject and not ctx.has_role("editor"):
        raise ForbiddenError("Không có quyền xem câu trả lời này")
    pending = t.status == "pending_review"
    return {"trace_id": t.trace_id, "status": t.status, "question": t.question,
            "answer": None if pending else t.answer, "citations": [] if pending else t.citations,
            "conflicts": t.conflicts, "artifacts": [] if pending else t.artifacts,
            "decision_trace": t.decision_trace if ctx.has_role("editor") or not pending else {},
            "created_at": t.created_at, "trace_url": trace_url(t.langfuse_trace_id)}
