"""Chat nhiều lượt (B7): session lưu Postgres (chat_sessions/chat_messages), gắn user_id + store_code.

Compaction khi lịch sử dài — cùng cơ chế với agent/loop.py (ước lượng token bằng _approx_tokens,
giữ NGUYÊN VĂN `keep_recent_messages` tin gần nhất, nén phần cũ hơn thành MỘT khối tóm tắt):
- tóm tắt bằng model rẻ (vai trò compaction, prompt chat_compaction) -> lưu chat_sessions.summary +
  summarized_upto_id (lần sau chỉ nén phần mới, không nén lại từ đầu);
- model lỗi -> fallback xác định: giữ nguyên văn câu hỏi user + 200 ký tự đầu mỗi câu trả lời (giống
  _extract_verbatim của agent: giữ quyết định, bỏ chi tiết dài) — không bao giờ chặn câu hỏi.
Lịch sử chỉ là ngữ cảnh hiểu câu hỏi (tham chiếu "nó", "cửa hàng đó"...), KHÔNG phải nguồn trích dẫn.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.loop import _approx_tokens
from core.config import AppConfig, read_prompt
from core.errors import ForbiddenError, NotFoundError
from core.i18n import L
from core.llm import LLMClient, LLMUnavailable
from core.logging import log_event
from core.models import ChatMessage, ChatSession, utcnow

logger = logging.getLogger(__name__)

_FOLLOWUP_RE = re.compile(r"^(còn|thế còn|vậy|còn ở|ở|cho|với|thì sao|sao|nó|cái đó|sản phẩm đó|mã đó|cửa hàng đó)\b|"
                          r"\b(thì sao|nó|cái đó|sản phẩm đó|mã đó|cửa hàng đó|như trên|vừa rồi)\b", re.IGNORECASE)


@dataclass
class ChatContext:
    session: ChatSession
    summary: str | None
    recent: list[ChatMessage]

    def prompt_messages(self) -> list[dict[str, str]]:
        """Lịch sử dạng messages cho model: (tóm tắt) + các lượt gần nhất (chỉ tin đã final)."""
        msgs: list[dict[str, str]] = []
        if self.summary:
            msgs.append({"role": "user", "content": f"(Tóm tắt hội thoại trước đó)\n{self.summary}"})
            msgs.append({"role": "assistant", "content": "Đã ghi nhận ngữ cảnh."})
        for m in self.recent:
            if m.status not in ("final",) or not m.content.strip():
                continue
            role = "assistant" if m.role == "assistant" else "user"
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + m.content
            else:
                msgs.append({"role": role, "content": m.content})
        if msgs and msgs[0]["role"] != "user":
            msgs.insert(0, {"role": "user", "content": "(bắt đầu hội thoại)"})
        if msgs and msgs[-1]["role"] == "user":
            msgs.append({"role": "assistant", "content": "(đang chờ câu hỏi tiếp)"})
        return msgs

    def last_user_question(self) -> str | None:
        for m in reversed(self.recent):
            if m.role == "user":
                return m.content
        return None


def retrieval_query(question: str, ctx: ChatContext | None) -> str:
    """Câu hỏi nối tiếp ngắn/đại từ ("còn ở HN01 thì sao?") -> ghép câu hỏi trước để retrieval đủ ý."""
    if ctx is None:
        return question
    prev = ctx.last_user_question()
    if prev and (len(question) < 60 and _FOLLOWUP_RE.search(question)):
        return f"{prev} {question}"
    return question


class ChatSessionService:
    def __init__(self, cfg: AppConfig, llm: LLMClient | None = None) -> None:
        self.cfg = cfg
        self.llm = llm

    def get_or_create(self, s: Session, session_id: str | None, *, workspace_id: str, user_id: str,
                      store_code: str | None) -> ChatSession:
        if session_id:
            sess = s.get(ChatSession, session_id)
            if sess is None:
                sess = ChatSession(id=session_id, workspace_id=workspace_id, user_id=user_id, store_code=store_code)
                s.add(sess)
                s.flush()
                return sess
            if sess.user_id != user_id:
                raise ForbiddenError(L("Session này thuộc người dùng khác.", "This session belongs to another user."), context={"session_id": session_id})
            if sess.workspace_id != workspace_id:
                raise ForbiddenError(L("Session thuộc workspace khác.", "This session belongs to another workspace."))
            if sess.store_code and store_code and sess.store_code != store_code:
                raise ForbiddenError(L(f"Session gắn với phạm vi {sess.store_code}, không dùng cho {store_code}.", f"This session is tied to scope {sess.store_code} and cannot be used for {store_code}."))
            return sess
        sess = ChatSession(workspace_id=workspace_id, user_id=user_id, store_code=store_code)
        s.add(sess)
        s.flush()
        return sess

    def get_owned(self, s: Session, session_id: str, user_id: str, *, workspace_id: str | None = None,
                  allow_any: bool = False) -> ChatSession:
        sess = s.get(ChatSession, session_id)
        if sess is None or (workspace_id and sess.workspace_id != workspace_id):
            raise NotFoundError(L(f"Không có session {session_id}", f"Session {session_id} not found"))
        if sess.user_id != user_id and not allow_any:
            raise ForbiddenError(L("Session này thuộc người dùng khác.", "This session belongs to another user."))
        return sess

    def add_message(self, s: Session, sess: ChatSession, role: str, content: str, *, trace_id: str | None = None,
                    status: str = "final", payload: dict[str, Any] | None = None) -> ChatMessage:
        msg = ChatMessage(session_id=sess.id, role=role, content=content, trace_id=trace_id, status=status,
                          payload=payload or {})
        s.add(msg)
        if role == "user" and not sess.title:
            sess.title = content[:80]
        sess.updated_at = utcnow()
        s.flush()
        return msg

    def load_context(self, s: Session, sess: ChatSession) -> ChatContext:
        q = select(ChatMessage).where(ChatMessage.session_id == sess.id)
        if sess.summarized_upto_id:
            q = q.where(ChatMessage.id > sess.summarized_upto_id)
        msgs = list(s.execute(q.order_by(ChatMessage.id)).scalars())
        ctx = ChatContext(session=sess, summary=sess.summary, recent=msgs)
        as_prompt = ctx.prompt_messages()
        if (len(msgs) > self.cfg.chat.history_max_messages
                or _approx_tokens(as_prompt) > self.cfg.chat.compaction_token_threshold):
            ctx = self._compact(s, sess, msgs)
        return ctx

    def _compact(self, s: Session, sess: ChatSession, msgs: list[ChatMessage]) -> ChatContext:
        keep = self.cfg.chat.keep_recent_messages
        old, recent = msgs[:-keep] if keep else msgs, msgs[-keep:] if keep else []
        if not old:
            return ChatContext(sess, sess.summary, msgs)
        transcript = "\n".join(f"{'Nhân viên' if m.role == 'user' else 'Trợ lý'}: {m.content}" for m in old if m.content)
        prior = f"Tóm tắt trước đó:\n{sess.summary}\n\n" if sess.summary else ""
        tokens_before = _approx_tokens([{"c": prior + transcript}])
        summary = None
        if self.llm is not None:
            try:
                res = self.llm.complete("compaction", system=read_prompt("chat_compaction", self.cfg),
                                        messages=[{"role": "user", "content": f"{prior}Hội thoại cần tóm tắt:\n{transcript}"}],
                                        max_tokens=600)
                summary = res.text.strip() or None
            except LLMUnavailable as e:
                log_event(logger, "chat_compaction_llm_failed", level=logging.WARNING, session_id=sess.id, error=str(e)[:200])
        if summary is None:  # fallback xác định
            lines = [sess.summary] if sess.summary else []
            for m in old:
                if m.role == "user":
                    lines.append(f"- Hỏi: {m.content.strip()}")
                elif m.content:
                    lines.append(f"  Đáp: {m.content.strip()[:200]}")
            summary = "\n".join(lines)
        sess.summary = summary
        sess.summarized_upto_id = old[-1].id
        s.flush()
        log_event(logger, "chat_compacted", session_id=sess.id, merged_messages=len(old), kept=len(recent),
                  tokens_before=tokens_before, tokens_after=_approx_tokens([{"c": summary}]))
        return ChatContext(sess, summary, recent)

    def list_messages(self, s: Session, sess: ChatSession, after_id: int | None = None) -> list[ChatMessage]:
        q = select(ChatMessage).where(ChatMessage.session_id == sess.id)
        if after_id:
            q = q.where(ChatMessage.id > after_id)
        return list(s.execute(q.order_by(ChatMessage.id)).scalars())
