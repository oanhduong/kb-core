"""Assistant agent: vòng lặp tool use (Anthropic) trên knowledge base của workspace.

Luồng 1 lượt hỏi:
    history (session, compaction) + câu hỏi -> model (stream) -> [tool_use]* -> chạy tool (assistant/tools.py,
    mọi dữ kiện thành nguồn [S#]) -> model tiếp ... -> câu trả lời cuối -> post-check citation bằng code
    (qa/citation.py) -> review (domain pack) -> decision trace (từ tool call THẬT) -> lưu qa_traces/chat.

Quy tắc citation: nếu có tool trả về nguồn mà câu trả lời không trích dẫn hợp lệ -> thay bằng thông báo
không đủ căn cứ (status no_citation). Không gọi tool nào (chào hỏi, hỏi về khả năng) -> trả lời tự do,
status answered_general. Tool có chạy nhưng không ra nguồn + model nói không tìm thấy -> abstained (ghi
bảng abstentions). Stream: text qua StreamingCitationGate (chỉ chảy khi đã có citation hợp lệ), các lượt
có tool phát event `status`; kết thúc luôn gửi `replace` nếu text đã phát khác câu trả lời cuối.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from assistant.sources import SourceRegistry
from assistant.tools import TOOL_SCHEMAS, TOOL_STATUS_VI, ToolContext, ToolOutcome, run_tool
from core.config import AppConfig, read_prompt
from core.errors import AppError
from core.llm import LLMClient, LLMUnavailable
from core.logging import log_context, log_event
from core.models import QATrace, new_id
from core.principal import WorkspaceContext
from domains import get_pack
from observability import tracing
from qa import citation as cit
from qa.abstention import record_abstention
from qa.review import enqueue_review, needs_review
from qa.retrieval import RetrievalUnavailable, VectorRetriever
from qa.sessions import ChatSessionService
from qa.trace import DecisionTrace, compute_confidence

logger = logging.getLogger(__name__)

MAX_TURNS = 8
ERROR_MESSAGE = ("Xin lỗi, hệ thống đang gặp sự cố tạm thời nên chưa trả lời được. Vui lòng thử lại sau ít phút. "
                 "(Mã tra cứu: {trace_id})")
_NOT_FOUND_RE = re.compile(r"không (tìm thấy|có) (thông tin|dữ liệu|tài liệu)|chưa có (tài liệu|dữ liệu)|không đủ (thông tin|căn cứ)", re.IGNORECASE)
_INTENT_BY_TOOLS = {
    frozenset(): "general", frozenset({"search_documents"}): "qa", frozenset({"search_documents", "read_source"}): "qa",
    frozenset({"list_documents"}): "overview", frozenset({"get_document_overview"}): "summary",
    frozenset({"list_documents", "get_document_overview"}): "summary", frozenset({"list_tables", "query_tables"}): "analytics",
    frozenset({"query_tables"}): "analytics", frozenset({"explore_graph"}): "relations",
}


class AssistantResponse(BaseModel):
    trace_id: str
    status: str  # answered | answered_general | abstained | no_citation | pending_review | error
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    intent: str | None = None
    confidence: dict[str, Any] = Field(default_factory=dict)
    review_id: str | None = None
    session_id: str | None = None
    message_id: int | None = None
    trace_url: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class AssistantRequest:
    question: str
    ctx: WorkspaceContext
    scope: str | None = None
    channel: str = "chat"  # chat | ask
    session_id: str | None = None
    document_ids: list[str] | None = None
    as_of: date | None = None

    @property
    def user_id(self) -> str:
        return self.ctx.subject


@dataclass
class _Run:
    trace_id: str
    req: AssistantRequest
    registry: SourceRegistry = field(default_factory=SourceRegistry)
    dtrace: DecisionTrace = field(default_factory=DecisionTrace)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    money: bool = False
    money_hits: list[str] = field(default_factory=list)
    top_score: float | None = None
    score_kind: str = "none"
    llm_calls: list[Any] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    turns: int = 0


class AssistantService:
    def __init__(self, cfg: AppConfig, sm: sessionmaker, llm: LLMClient, retriever: VectorRetriever) -> None:
        self.cfg = cfg
        self.sm = sm
        self.llm = llm
        self.retriever = retriever
        self.sessions = ChatSessionService(cfg, llm)

    # ------------------------------------------------------------------ chuẩn bị
    def _prepare(self, req: AssistantRequest, trace_id: str) -> _Run:
        run = _Run(trace_id=trace_id, req=req)
        with self.sm() as s:
            if req.channel == "chat":
                sess = self.sessions.get_or_create(s, req.session_id, workspace_id=req.ctx.workspace_id, user_id=req.user_id, store_code=req.scope)
                run.session_id = sess.id
                th = tracing.current_trace()
                if th is not None:
                    th.update(session_id=sess.id, metadata={"session_id": sess.id})
                ctx = self.sessions.load_context(s, sess)
                run.history = ctx.prompt_messages()
                self.sessions.add_message(s, sess, "user", req.question, trace_id=trace_id)
                s.commit()
        run.money, run.money_hits = needs_review(req.question, req.ctx.role, self.cfg, req.ctx.domain_pack)
        run.dtrace.understood = {"question": req.question, "workspace_id": req.ctx.workspace_id, "scope": req.scope,
                                 "document_ids": req.document_ids, "money_topic": run.money, "money_keywords": run.money_hits,
                                 "store_scope": f"{req.scope} + dữ liệu toàn workspace" if req.scope else "toàn workspace"}
        return run

    def _system(self, req: AssistantRequest) -> list[dict[str, Any]]:
        pack = get_pack(req.ctx.domain_pack)
        extra = f"\n\nWorkspace: {req.ctx.workspace_name} (lĩnh vực: {pack.name}). Hôm nay: {date.today().isoformat()}."
        if pack.assistant_hints:
            extra += "\nKiến thức nghiệp vụ:\n" + "\n".join(f"- {h}" for h in pack.assistant_hints)
        if req.scope:
            extra += f" Người hỏi thuộc phạm vi {req.scope}: chỉ dùng dữ liệu của phạm vi đó hoặc toàn hệ thống."
        if req.document_ids:
            extra += f" Người dùng đang chọn {len(req.document_ids)} tài liệu cụ thể — ưu tiên tìm trong đó (document_ids đã được gắn sẵn cho search_documents)."
        return [{"type": "text", "text": read_prompt("assistant_system", self.cfg), "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": extra}]

    # ------------------------------------------------------------------ vòng lặp
    def _loop(self, run: _Run, emit) -> tuple[str, Any]:
        """-> (text cuối cùng của model, LLMResult cuối). emit(event, data) cho stream (None khi one-shot)."""
        req = run.req
        system = self._system(req)
        messages: list[dict[str, Any]] = [*run.history, {"role": "user", "content": req.question}]
        gate = cit.StreamingCitationGate(set())
        final_text = ""
        last_result = None
        for turn in range(MAX_TURNS):
            run.turns = turn + 1
            gate.reset_pending()
            with tracing.span("assistant.turn", turn=turn + 1):
                resp = self._call_model(system, messages, TOOL_SCHEMAS if turn < MAX_TURNS - 1 else None, gate, emit, run)
            last_result = resp
            run.llm_calls.append(resp["call"])
            text = resp["text"]
            tool_uses = resp["tool_uses"]
            if not tool_uses:
                final_text = text
                break
            messages.append({"role": "assistant", "content": resp["content"]})
            results = []
            with self.sm() as s:
                tc = ToolContext(cfg=self.cfg, session=s, ctx=req.ctx, retriever=self.retriever, registry=run.registry, scope=req.scope,
                                 document_ids=req.document_ids)
                for tu in tool_uses:
                    if emit:
                        emit("status", {"tool": tu["name"], "message": TOOL_STATUS_VI.get(tu["name"], "Đang xử lý…"), "args": _public_args(tu["input"])})
                    start = time.perf_counter()
                    out: ToolOutcome = run_tool(tc, tu["name"], dict(tu["input"]))
                    rec = {"tool": tu["name"], "args": _public_args(tu["input"]), "elapsed_ms": int((time.perf_counter() - start) * 1000),
                           "sources": [src.sid for src in out.sources], "summary": out.summary, "error": out.error}
                    run.tool_calls.append(rec)
                    run.dtrace.add_search(_search_label(tu["name"], tu["input"]), result=_result_label(out), tool=tu["name"], **{k: v for k, v in out.summary.items() if k != "sql"})
                    if out.error:
                        run.dtrace.degrade(f"{tu['name']}: {out.error[:120]}")
                    for art in out.artifacts:
                        run.artifacts.append(art)
                        if emit:
                            emit("artifact", art)
                    if out.top_score is not None and (run.top_score is None or out.top_score > run.top_score):
                        run.top_score, run.score_kind = out.top_score, out.score_kind or run.score_kind
                    elif out.score_kind and run.score_kind == "none":
                        run.score_kind = out.score_kind
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out.text[:30000], "is_error": bool(out.error)})
                gate.valid = {src.sid for src in run.registry.all()}
            messages.append({"role": "user", "content": results})
        else:
            final_text = final_text or (last_result["text"] if last_result else "")
        if emit:
            tail = gate.flush()
            if tail:
                emit("delta", {"text": tail})
        run.dtrace.understood["gate_emitted_chars"] = len(gate.emitted)
        return final_text, last_result

    def _call_model(self, system, messages, tools, gate, emit, run) -> dict[str, Any]:
        """Gọi model 1 lượt (stream). Trả {text, tool_uses[{id,name,input}], content(blocks), call}."""
        kwargs: dict[str, Any] = {"model": self.llm.model_for("assistant"), "max_tokens": self.cfg.llm.assistant_max_tokens,
                                  "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        attempts = self.cfg.llm.max_retries + 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            start = time.perf_counter()
            emitted = False
            try:
                with self.llm._client.messages.stream(**kwargs) as stream:  # noqa: SLF001 — cùng client/timeout của LLMClient
                    for text in stream.text_stream:
                        if emit:
                            out = gate.feed(text)
                            if out:
                                emitted = True
                                emit("delta", {"text": out})
                    final = stream.get_final_message()
                latency = int((time.perf_counter() - start) * 1000)
                text = "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", None) == "text")
                tool_uses = [{"id": b.id, "name": b.name, "input": dict(b.input or {})} for b in final.content if getattr(b, "type", None) == "tool_use"]
                call = self.llm._finish_call("assistant", kwargs["model"], final.usage, latency, "ok",  # noqa: SLF001
                                             {"messages": len(messages), "tools": len(tool_uses)}, text[:2000] or json.dumps([t["name"] for t in tool_uses]))
                # dựng lại block THỦ CÔNG — model_dump() của SDK có thêm field (parsed_output, citations...) mà API từ chối
                content: list[dict[str, Any]] = []
                for b in final.content:
                    btype = getattr(b, "type", None)
                    if btype == "text" and getattr(b, "text", ""):
                        content.append({"type": "text", "text": b.text})
                    elif btype == "tool_use":
                        content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input or {})})
                return {"text": text, "tool_uses": tool_uses, "content": content, "call": call}
            except Exception as e:
                from core.llm import _is_retryable

                last_exc = e
                retryable = _is_retryable(e) and not emitted
                log_event(logger, "assistant_model_failed", level=logging.WARNING, attempt=attempt + 1, retryable=retryable,
                          error=f"{type(e).__name__}: {e}"[:300])
                if not retryable or attempt == attempts - 1:
                    break
                self.llm._sleep(self.llm._backoff(attempt))  # noqa: SLF001
        raise LLMUnavailable(f"Mô hình AI không phản hồi sau {attempts} lần thử: {type(last_exc).__name__}",
                             context={"error": str(last_exc)}) from last_exc

    # ------------------------------------------------------------------ hoàn tất
    def _finalize(self, run: _Run, raw_text: str, llm_result: Any) -> AssistantResponse:
        sources = run.registry.as_mapping()
        tools_used = frozenset(tc["tool"] for tc in run.tool_calls)
        intent = _INTENT_BY_TOOLS.get(tools_used, "mixed" if len(tools_used) > 1 else (next(iter(tools_used)) if tools_used else "general"))
        pc = cit.postcheck(raw_text, sources, min_coverage=self.cfg.citation.min_sentence_coverage,
                           insufficient_message=self.cfg.citation.insufficient_message) if sources else None
        if not run.tool_calls:
            status, answer = "answered_general", raw_text.strip()
            citations: list[dict[str, Any]] = []
        elif not sources:
            status = "abstained" if _NOT_FOUND_RE.search(raw_text or "") or not raw_text.strip() else "answered_general"
            answer = raw_text.strip() or self.cfg.abstention.handoff_message
            citations = []
            if status == "abstained":
                run.dtrace.degrade("Các công cụ không trả về nguồn nào phù hợp")
        elif pc and pc.ok:
            status, answer, citations = "answered", pc.answer, [c.to_dict() for c in pc.citations]
        else:
            status, answer, citations = "no_citation", (pc.answer if pc else self.cfg.citation.insufficient_message), []
        if status == "answered" and run.money:
            status = "pending_review"
        return self._persist(run, status, answer, citations, pc, llm_result, intent)

    def _persist(self, run: _Run, status: str, answer: str, citations: list[dict[str, Any]], pc: Any, llm_result: Any, intent: str,
                 error: str | None = None) -> AssistantResponse:
        req, dtrace = run.req, run.dtrace
        used = set(pc.used_sids) if pc and pc.ok else set()
        dtrace.found = [src.to_found(src.sid in used) for src in run.registry.all()]
        dtrace.basis = [f"[{c['n']}] {c['label']}" for c in citations]
        dtrace.understood.update({"question_type": intent, "route": "agent", "tools_used": [t["tool"] for t in run.tool_calls], "turns": run.turns})
        coverage = pc.coverage if pc and pc.ok else (1.0 if status == "answered_general" else 0.0)
        if status in ("answered", "pending_review", "answered_general"):
            if run.score_kind == "sql":  # số liệu do SQL tính trên bảng thật — không phụ thuộc điểm retrieval
                score = max(0.0, min(1.0, 0.9 * (0.6 + 0.4 * coverage) - 0.1 * min(len(dtrace.degraded), 2)))
                conf = {"level": "cao" if score >= 0.7 else "trung bình", "score": round(score, 3),
                        "explanation": "số liệu tính trực tiếp bằng SQL trên bảng đã trích" + ("" if coverage >= 0.8 else f"; {coverage:.0%} câu khẳng định có trích dẫn")}
            else:
                conf = compute_confidence(top_score=run.top_score, score_kind=run.score_kind, coverage=coverage, conflicts=0,
                                          degraded=len(dtrace.degraded))
            if status == "answered_general":
                conf = {"level": "không áp dụng", "score": None, "explanation": "trả lời chung, không dựa trên tài liệu"}
        else:
            conf = {"level": "không áp dụng", "score": None, "explanation": status}
        dtrace.confidence = conf
        dtrace.outcome = {"answered": "Đã trả lời có trích dẫn", "answered_general": "Trả lời chung (không cần nguồn)",
                          "abstained": "Không tìm thấy căn cứ trong tài liệu", "no_citation": "Không trả lời: bản nháp của model không có trích dẫn hợp lệ",
                          "pending_review": "Chờ người phụ trách duyệt (câu hỏi liên quan tiền)", "error": "Lỗi hệ thống — đã trả thông báo thân thiện"}.get(status, status)
        trace_dict = dtrace.to_dict()
        cost = sum(c.cost_usd for c in run.llm_calls)
        in_tok = sum(c.input_tokens for c in run.llm_calls)
        out_tok = sum(c.output_tokens for c in run.llm_calls)
        review_id = message_id = None
        user_answer = self.cfg.review.pending_message if status == "pending_review" else answer
        with self.sm() as s:
            if req.channel == "chat" and run.session_id:
                sess = self.sessions.get_owned(s, run.session_id, req.user_id, workspace_id=req.ctx.workspace_id)
                msg = self.sessions.add_message(s, sess, "assistant", user_answer, trace_id=run.trace_id,
                                                status={"pending_review": "pending_review", "error": "error"}.get(status, "final"),
                                                payload={"citations": citations if status != "pending_review" else [],
                                                         "artifacts": run.artifacts if status != "pending_review" else [], "status": status, "intent": intent})
                message_id = msg.id
            if status == "pending_review":
                item = enqueue_review(s, trace_id=run.trace_id, session_id=run.session_id, message_id=message_id, user_id=req.user_id,
                                      store_code=req.scope, question=req.question, draft_answer=answer, citations=citations,
                                      decision_trace=trace_dict, reason="Câu hỏi liên quan tiền: " + ", ".join(run.money_hits),
                                      workspace_id=req.ctx.workspace_id)
                review_id = item.id
            if status in ("abstained", "no_citation"):
                record_abstention(s, trace_id=run.trace_id, question=req.question, reason=status if status == "no_citation" else "no_results",
                                  top_score=run.top_score, question_type=intent, store_code=req.scope, user_id=req.user_id,
                                  workspace_id=req.ctx.workspace_id)
            s.add(QATrace(trace_id=run.trace_id, workspace_id=req.ctx.workspace_id, channel=req.channel, session_id=run.session_id,
                          user_id=req.user_id, role=req.ctx.role, store_code=req.scope, question=req.question, question_type=intent,
                          route="agent", status=status, answer=answer, citations=citations, decision_trace=trace_dict, conflicts=[],
                          confidence=conf.get("score"), top_score=run.top_score, config_version=self.cfg.config_version,
                          config_hash=self.cfg.config_hash, latency_ms=int((time.perf_counter() - run.started) * 1000), cost_usd=cost,
                          input_tokens=in_tok, output_tokens=out_tok, tool_calls=run.tool_calls, artifacts=run.artifacts,
                          langfuse_trace_id=run.trace_id if (tracing.current_trace() and tracing.current_trace().enabled) else None, error=error))
            s.commit()
        th = tracing.current_trace()
        if th is not None:
            th.update(output={"status": status, "answer": user_answer[:2000]}, metadata={"status": status, "question_type": intent, "confidence": conf.get("level")})
        log_event(logger, "assistant_done", status=status, intent=intent, tools=[t["tool"] for t in run.tool_calls], sources=len(run.registry),
                  citations=len(citations), cost_usd=round(cost, 5), latency_ms=int((time.perf_counter() - run.started) * 1000))
        return AssistantResponse(trace_id=run.trace_id, status=status, answer=user_answer, citations=citations if status != "pending_review" else [],
                                 artifacts=run.artifacts if status != "pending_review" else [], decision_trace=trace_dict, intent=intent,
                                 confidence=conf, review_id=review_id, session_id=run.session_id, message_id=message_id,
                                 trace_url=tracing.trace_url(run.trace_id) if th is not None and th.enabled else None, tool_calls=run.tool_calls)

    def _error(self, req: AssistantRequest, trace_id: str, run: _Run | None, exc: BaseException) -> AssistantResponse:
        msg = ERROR_MESSAGE.format(trace_id=trace_id)
        if isinstance(exc, AppError) and exc.http_status == 503:
            msg = f"{exc.message} (Mã tra cứu: {trace_id})"
        run = run or _Run(trace_id=trace_id, req=req)
        run.dtrace.degrade(f"{type(exc).__name__}: {str(exc)[:200]}")
        try:
            return self._persist(run, "error", msg, [], None, None, "error", error=f"{type(exc).__name__}: {exc}"[:2000])
        except Exception:
            logger.exception("không lưu được trace lỗi")
            return AssistantResponse(trace_id=trace_id, status="error", answer=msg, session_id=run.session_id)

    def _meta(self, req: AssistantRequest) -> dict[str, Any]:
        return {"workspace_id": req.ctx.workspace_id, "scope": req.scope, "user_id": req.user_id, "role": req.ctx.role,
                "session_id": req.session_id, "channel": req.channel, "domain_pack": req.ctx.domain_pack}

    # ------------------------------------------------------------------ public
    def answer(self, req: AssistantRequest) -> AssistantResponse:
        trace_id = new_id()
        with log_context(trace_id=trace_id, user_id=req.user_id, store_code=req.scope, session_id=req.session_id, config_version=self.cfg.config_version), \
                tracing.start_trace(f"assistant.{req.channel}", trace_id=trace_id, user_id=req.user_id, session_id=req.session_id,
                                    input=req.question, metadata=self._meta(req)):
            run: _Run | None = None
            try:
                run = self._prepare(req, trace_id)
                text, result = self._loop(run, None)
                return self._finalize(run, text, result)
            except (LLMUnavailable, RetrievalUnavailable) as e:
                log_event(logger, "assistant_upstream_unavailable", level=logging.ERROR, error=str(e))
                return self._error(req, trace_id, run, e)
            except AppError as e:
                if e.http_status < 500:
                    raise
                logger.exception("assistant AppError")
                return self._error(req, trace_id, run, e)
            except Exception as e:
                logger.exception("assistant lỗi không lường trước")
                return self._error(req, trace_id, run, e)

    def stream(self, req: AssistantRequest) -> Iterator[tuple[str, dict[str, Any]]]:
        """SSE: meta -> (status | artifact | delta)* -> [replace] -> [pending_review] -> citations -> trace -> done."""
        trace_id = new_id()
        with log_context(trace_id=trace_id, user_id=req.user_id, store_code=req.scope, session_id=req.session_id, config_version=self.cfg.config_version), \
                tracing.start_trace(f"assistant.{req.channel}", trace_id=trace_id, user_id=req.user_id, session_id=req.session_id,
                                    input=req.question, metadata=self._meta(req)):
            run: _Run | None = None
            emitted: list[str] = []
            try:
                run = self._prepare(req, trace_id)
                yield "meta", {"trace_id": trace_id, "session_id": run.session_id}
                holder: list[Any] = []
                for ev, data in self._loop_events(run, holder):
                    if ev == "delta":
                        emitted.append(data["text"])
                    yield ev, data
                text, result = holder[0]
                resp = self._finalize(run, text, result)
            except (LLMUnavailable, RetrievalUnavailable) as e:
                log_event(logger, "assistant_upstream_unavailable", level=logging.ERROR, error=str(e))
                resp = self._error(req, trace_id, run, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}
                return
            except AppError as e:
                if e.http_status < 500:
                    yield "error", {"message": e.message, "code": e.code, "trace_id": trace_id}
                    yield "done", {"status": "error", "trace_id": trace_id}
                    return
                logger.exception("assistant stream AppError")
                resp = self._error(req, trace_id, run, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}
                return
            except Exception as e:
                logger.exception("assistant stream lỗi")
                resp = self._error(req, trace_id, run, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}
                return
            if resp.answer.strip() != "".join(emitted).strip():
                yield "replace", {"text": resp.answer}
            if resp.status == "pending_review":
                yield "pending_review", {"review_id": resp.review_id, "message": resp.answer, "message_id": resp.message_id}
            yield "citations", {"citations": resp.citations}
            yield "trace", {"decision_trace": resp.decision_trace, "confidence": resp.confidence, "trace_url": resp.trace_url, "intent": resp.intent}
            yield "done", {"status": resp.status, "trace_id": resp.trace_id, "answer": resp.answer, "review_id": resp.review_id,
                           "session_id": resp.session_id, "message_id": resp.message_id, "artifacts": resp.artifacts}

    def _loop_events(self, run: _Run, holder: list[Any]) -> Iterator[tuple[str, dict[str, Any]]]:
        """Chạy _loop ở thread riêng (copy contextvars để giữ trace), đẩy event qua queue để yield ngay;
        kết quả (text, LLMResult) đặt vào holder[0], exception được ném lại ở luồng gọi."""
        import contextvars
        import queue
        import threading

        q: queue.Queue = queue.Queue()
        done = object()

        def _work() -> None:
            try:
                holder.append(self._loop(run, lambda ev, data: q.put((ev, data))))
            except BaseException as e:
                holder.append(e)
            finally:
                q.put(done)

        threading.Thread(target=contextvars.copy_context().run, args=(_work,), daemon=True, name="assistant-loop").start()
        while True:
            item = q.get()
            if item is done:
                break
            yield item
        if holder and isinstance(holder[0], BaseException):
            raise holder[0]
        if not holder:
            raise LLMUnavailable("Agent kết thúc không có kết quả")


def _public_args(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        out[k] = v if not isinstance(v, str) or len(v) <= 300 else v[:300] + "…"
    return out


def _search_label(tool: str, args: dict[str, Any]) -> str:
    if tool == "search_documents":
        return f"Tìm trong tài liệu: “{args.get('query', '')[:80]}”"
    if tool == "query_tables":
        return "Truy vấn số liệu (SQL): " + (args.get("purpose") or args.get("sql", "")[:80])
    if tool == "explore_graph":
        return f"Đồ thị quan hệ quanh “{args.get('entity', '')}”"
    if tool == "get_document_overview":
        return "Tổng quan tài liệu"
    if tool == "list_documents":
        return "Danh sách tài liệu trong workspace"
    if tool == "list_tables":
        return "Catalog bảng số liệu"
    if tool == "read_source":
        return f"Đọc chi tiết nguồn {args.get('source_id', '')}"
    return tool


def _result_label(out: ToolOutcome) -> str:
    if out.error:
        return f"lỗi: {out.error[:80]}"
    if out.sources:
        return f"{len(out.sources)} nguồn"
    return "không có nguồn"
