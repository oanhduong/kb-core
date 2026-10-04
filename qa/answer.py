"""QA service đầy đủ (B1–B9):

    router (A5) -> [vector: hybrid + metadata pre-filter + rerank] | [graph local search / aggregation]
    -> lấy parent chunk -> phát hiện mâu thuẫn (B4) -> abstention (B3) -> assemble context (nội dung
    bọc untrusted) -> gọi model (stream hoặc không) -> post-check citation bằng code (B2) -> công bố
    mâu thuẫn -> review queue nếu là câu hỏi tiền (B6) -> decision trace (B5) -> lưu qa_traces/chat.

Graceful degradation (B9): reranker lỗi -> hybrid; graph lỗi/rỗng -> vector; model timeout -> retry 2
lần backoff (core/llm.py) rồi trả thông báo rõ ràng kèm trace_id; mọi exception khác -> thông báo
thân thiện + log đầy đủ, KHÔNG BAO GIỜ để API trả 500 trần.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from core.config import AppConfig, read_prompt
from core.errors import AppError
from core.llm import LLMClient, LLMUnavailable
from core.logging import log_context, log_event
from core.models import QATrace, new_id
from core.principal import Principal
from graph.query import GraphSearcher
from graph.resolve import get_resolver
from graph.router import QuestionRouter, RouteDecision
from graph.store import GraphStore, GraphUnavailable
from index.store import Filters
from kb import repository as repo
from observability import tracing
from qa import citation as cit
from qa.abstention import AbstainDecision, decide, record_abstention
from qa.conflicts import ConflictInfo, conflict_prompt_block, detect_conflicts, ensure_conflict_disclosure, extract_price_facts, persist_conflicts
from qa.dates import extract_as_of
from qa.retrieval import RetrievalOutcome, RetrievalUnavailable, VectorRetriever
from qa.review import enqueue_review, needs_review
from qa.sessions import ChatContext, ChatSessionService, retrieval_query
from qa.sources import Source, build_sources, build_user_prompt
from qa.trace import DecisionTrace, compute_confidence

logger = logging.getLogger(__name__)

_SKU_IN_Q_RE = re.compile(r"\b[A-Za-zĐđ]{2,5}-?\d{2,6}\b")
ERROR_MESSAGE = ("Xin lỗi, hệ thống đang gặp sự cố tạm thời nên chưa trả lời được. Vui lòng thử lại sau ít phút; "
                 "nếu cần gấp hãy liên hệ supervisor. (Mã tra cứu: {trace_id})")


class QAResponse(BaseModel):
    trace_id: str
    status: str  # answered | abstained | pending_review | no_citation | error
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    question_type: str | None = None
    route: str | None = None
    confidence: dict[str, Any] = Field(default_factory=dict)
    review_id: str | None = None
    session_id: str | None = None
    message_id: int | None = None
    trace_url: str | None = None


@dataclass
class QARequest:
    question: str
    principal: Principal
    store_code: str | None
    channel: str = "ask"  # ask | chat
    session_id: str | None = None
    as_of: date | None = None


@dataclass
class Prepared:
    trace_id: str
    req: QARequest
    dtrace: DecisionTrace
    route: RouteDecision | None = None
    as_of: date | None = None
    filters: Filters | None = None
    sources: list[Source] = field(default_factory=list)
    conflicts: list[ConflictInfo] = field(default_factory=list)
    computed_facts: list[str] = field(default_factory=list)
    graph_paths: list[str] = field(default_factory=list)
    abstain: AbstainDecision | None = None
    score_kind: str = "none"
    top_score: float | None = None
    money: bool = False
    money_hits: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    session_id: str | None = None
    started: float = field(default_factory=time.perf_counter)


class QAService:
    def __init__(self, cfg: AppConfig, sm: sessionmaker, llm: LLMClient, retriever: VectorRetriever,
                 graph_store_provider: Callable[[], GraphStore | None]) -> None:
        self.cfg = cfg
        self.sm = sm
        self.llm = llm
        self.retriever = retriever
        self.graph_store_provider = graph_store_provider
        self.sessions = ChatSessionService(cfg, llm)

    # ================================================================== PREPARE
    def _filters(self, store_code: str | None, as_of: date | None) -> Filters:
        stores = [store_code, None] if store_code else None
        if as_of:
            return Filters(store_code=stores, as_of=as_of.isoformat())
        return Filters(store_code=stores, is_active=True)

    def _graph(self, s: Session, dtrace: DecisionTrace) -> GraphSearcher | None:
        if not (self.cfg.graph.enabled and self.cfg.router.graph_enabled):
            return None
        try:
            store = self.graph_store_provider()
            if store is None:
                return None
            resolver = get_resolver(store, s, kb_version=repo.current_kb_version(s))
            return GraphSearcher(store, resolver, self.llm, self.cfg)
        except GraphUnavailable as e:
            dtrace.degrade("Graph (Apache AGE) không truy cập được -> mọi câu hỏi đi tìm kiếm văn bản")
            log_event(logger, "graph_unavailable", level=logging.ERROR, error=str(e)[:300])
            return None

    def _prepare(self, req: QARequest, trace_id: str) -> Prepared:
        cfg = self.cfg
        dtrace = DecisionTrace()
        prep = Prepared(trace_id=trace_id, req=req, dtrace=dtrace)
        with self.sm() as s:
            chat_ctx: ChatContext | None = None
            if req.channel == "chat":
                sess = self.sessions.get_or_create(s, req.session_id, user_id=req.principal.subject, store_code=req.store_code)
                prep.session_id = sess.id
                th = tracing.current_trace()
                if th is not None:  # E2: session mới tạo trong request -> gắn session_id thật lên trace
                    th.update(session_id=sess.id, metadata={"session_id": sess.id})
                chat_ctx = self.sessions.load_context(s, sess)
                prep.history = chat_ctx.prompt_messages()
                self.sessions.add_message(s, sess, "user", req.question, trace_id=trace_id)
                s.commit()
            rq = retrieval_query(req.question, chat_ctx)
            prep.as_of = req.as_of or extract_as_of(req.question)
            prep.filters = self._filters(req.store_code, prep.as_of)
            prep.money, prep.money_hits = needs_review(req.question, req.principal.role, cfg)

            searcher = self._graph(s, dtrace)
            with tracing.span("router", input={"question": req.question}) as sp:
                route = QuestionRouter(cfg, self.llm, searcher).route(rq)
                sp.update(output=route.to_dict())
            prep.route = route
            dtrace.understood = {
                "question": req.question, "retrieval_query": rq if rq != req.question else None,
                "question_type": route.question_type, "route": route.route, "router_method": route.method,
                "router_confidence": round(route.confidence, 2), "router_reasons": route.reasons,
                "entities": [{"type": m.label, "id": m.id, "text": m.text} for m in route.entities],
                "store_scope": f"{req.store_code} + dữ liệu toàn hệ thống" if req.store_code else "toàn hệ thống",
                "as_of": prep.as_of.isoformat() if prep.as_of else None,
                "money_topic": prep.money, "money_keywords": prep.money_hits,
            }
            tracing.current_trace() and tracing.current_trace().update(metadata={"question_type": route.question_type, "route": route.route})

            graph_chunks: list[Any] = []
            if route.route in ("graph", "graph_aggregation") and searcher is not None:
                graph_chunks = self._graph_search(s, searcher, route, req, prep)
                if not graph_chunks and not prep.computed_facts:
                    # route báo đường đi THỰC TẾ (kế hoạch ban đầu giữ trong decision trace)
                    dtrace.understood["planned_route"] = route.route
                    route.route = "vector"
                    dtrace.understood["route"] = "vector"
            outcome: RetrievalOutcome | None = None
            vector_chunks: list[Any] = []
            score_by_chunk: dict[str, float | None] = {}
            if not graph_chunks or len(graph_chunks) < 2:
                outcome = self.retriever.retrieve(rq, prep.filters)
                for d in outcome.degraded:
                    dtrace.degrade(d)
                child_ids = [c.chunk_id for c in outcome.candidates]
                vector_chunks = repo.context_chunks_for_child_ids(s, child_ids)
                children = repo.get_chunks(s, child_ids)
                for c in outcome.candidates:
                    ch = children.get(c.chunk_id)
                    target = (ch.parent_chunk_id if ch and ch.role == "child" and ch.parent_chunk_id else c.chunk_id)
                    sc = c.rerank_score if c.rerank_score is not None else c.dense_score
                    if target not in score_by_chunk or (sc or 0) > (score_by_chunk[target] or 0):
                        score_by_chunk[target] = sc
                dtrace.add_search(
                    "Tìm kiếm tài liệu (hybrid: ngữ nghĩa + từ khoá, có rerank)" if outcome.rerank_used else "Tìm kiếm tài liệu (hybrid: ngữ nghĩa + từ khoá)",
                    filters=_filters_vi(prep.filters), dense_hits=outcome.dense_count, bm25_hits=outcome.bm25_count,
                    candidates=len(outcome.candidates), rerank=outcome.rerank_used,
                    result=f"{len(vector_chunks)} đoạn ngữ cảnh" + (" (bổ sung cho graph)" if graph_chunks else ""))

            origin = {c.chunk_id: "graph" for c in graph_chunks}
            chunks = graph_chunks + [c for c in vector_chunks if c.chunk_id not in origin]
            docs = repo.get_documents(s, {c.document_id for c in chunks})
            prep.sources = build_sources(chunks, docs, origin_by_chunk=origin, score_by_chunk=score_by_chunk,
                                         max_sources=max(cfg.retrieval.top_k, cfg.graph.max_chunks if graph_chunks else 0) + 2,
                                         max_chars=cfg.retrieval.max_context_chars)

            # mâu thuẫn (B4): từ record nguồn + mọi record đang hiệu lực cùng SKU trong phạm vi store
            self._conflicts(s, prep, req)

            strong_bm25 = 0
            if outcome is not None and outcome.candidates:
                codes = {c.upper() for c in _SKU_IN_Q_RE.findall(req.question)}
                strong_bm25 = int(any(code in (c.text or "").upper() for c in outcome.candidates[:3] if c.bm25_score for code in codes))
            prep.abstain = decide(cfg, candidates=len(vector_chunks), rerank_top=outcome.rerank_top if outcome else None,
                                  dense_top=outcome.dense_top if outcome else None, bm25_hits=strong_bm25,
                                  graph_sources=len(graph_chunks) + (1 if prep.computed_facts else 0))
            prep.score_kind, prep.top_score = prep.abstain.score_kind, prep.abstain.top_score
            dtrace.found = [src.to_found(False) for src in prep.sources]
        return prep

    def _graph_search(self, s: Session, searcher: GraphSearcher, route: RouteDecision, req: QARequest,
                      prep: Prepared) -> list[Any]:
        dtrace = prep.dtrace
        try:
            with tracing.span("graph.search", route=route.route) as sp:
                mentions, method = route.entities, route.extraction_method
                if not mentions:
                    mentions, method = searcher.extract_entities(req.question, allow_llm=True)
                if route.route == "graph_aggregation":
                    res = searcher.aggregate(req.question, mentions, store_code=req.store_code, extraction_method=method)
                else:
                    res = searcher.local_search(req.question, mentions, store_code=req.store_code, extraction_method=method)
                record_ids = repo.resolve_record_refs(s, res.record_ids)
                chunks = repo.context_chunks_for_records(s, record_ids, include_inactive=bool(prep.as_of))
                allowed = {req.store_code, None} if req.store_code else None
                chunks = [c for c in chunks if allowed is None or c.store_code in allowed][: self.cfg.graph.max_chunks]
                sp.update(output={"entities": len(mentions), "paths": len(res.paths), "records": len(record_ids),
                                  "chunks": len(chunks), "facts": len(res.computed_facts)})
            prep.computed_facts = res.computed_facts
            prep.graph_paths = [p.explanation for p in res.paths[:10]]
            dtrace.graph_paths = prep.graph_paths
            dtrace.computed_facts = res.computed_facts
            dtrace.add_search(
                "Đồ thị tri thức (graph) — " + ("đếm/liệt kê" if res.mode == "aggregation" else f"mở rộng tối đa {self.cfg.graph.max_hops} bước"),
                start_nodes=[f"{m.label} {m.id}" for m in mentions], entity_extraction=method,
                paths=len(res.paths), aggregation=res.aggregation,
                result=f"{len(chunks)} đoạn nguồn" + (f", {len(res.paths)} đường đi" if res.paths else ""))
            if not chunks and not res.computed_facts:
                dtrace.degrade("Graph không tìm thấy đường đi phù hợp -> chuyển sang tìm kiếm văn bản")
            elif len(chunks) < 2:
                dtrace.degrade("Graph có ít nguồn -> bổ sung thêm từ tìm kiếm văn bản")
            for w in res.warnings:
                dtrace.add_search("Graph — ghi chú", result=w)
            return chunks
        except GraphUnavailable as e:
            dtrace.degrade("Graph lỗi khi truy vấn -> chuyển sang tìm kiếm văn bản")
            log_event(logger, "graph_query_failed", level=logging.ERROR, error=str(e)[:300])
            return []

    def _conflicts(self, s: Session, prep: Prepared, req: QARequest) -> None:
        if not prep.sources:
            return
        recs = repo.get_records(s, [src.record_id for src in prep.sources])
        skus = sorted({k for r in recs.values() for k in (r.skus or [])})
        stores = [req.store_code, None] if req.store_code else None
        extra = repo.active_price_records_for_skus(s, skus[:20], stores) if skus and not prep.as_of else []
        label_by_record = {src.record_id: src.label for src in prep.sources}
        docs = repo.get_documents(s, {r.document_id for r in extra})
        facts = []
        seen: set[str] = set()
        for r in list(recs.values()) + extra:
            if r.record_id in seen:
                continue
            seen.add(r.record_id)
            if prep.as_of is None and not r.is_active:
                continue
            doc = docs.get(r.document_id)
            label = label_by_record.get(r.record_id) or (cit.format_citation(doc.title, doc.source_type, r.location) if doc else r.source_file)
            facts.extend(extract_price_facts(r, label))
        prep.conflicts = detect_conflicts(facts)
        if not prep.conflicts:
            return
        # record mâu thuẫn chưa nằm trong nguồn -> thêm vào để model trích dẫn được CẢ HAI phía
        have = {src.record_id for src in prep.sources}
        missing = [v.record_id for c in prep.conflicts for v in c.values if v.record_id not in have]
        if missing:
            add_chunks = repo.context_chunks_for_records(s, missing)
            add_docs = repo.get_documents(s, {c.document_id for c in add_chunks})
            extra_sources = build_sources(add_chunks, add_docs, origin_by_chunk={}, score_by_chunk={},
                                          max_sources=len(add_chunks), max_chars=4000)
            for i, src in enumerate(extra_sources, start=len(prep.sources) + 1):
                src.sid = f"S{i}"
                src.origin = "conflict_check"
            prep.sources.extend(extra_sources)
        prep.dtrace.conflicts = [c.to_dict() for c in prep.conflicts]

    # ================================================================== MODEL
    def _messages(self, prep: Prepared) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        sid_by_record = {src.record_id: src.sid for src in prep.sources}
        user_prompt = build_user_prompt(
            prep.req.question, prep.sources, store_code=prep.req.store_code,
            as_of=prep.as_of.isoformat() if prep.as_of else None,
            conflicts_block=conflict_prompt_block(prep.conflicts, sid_by_record) if prep.conflicts else None,
            computed_facts=prep.computed_facts, graph_paths=prep.graph_paths)
        system = [{"type": "text", "text": read_prompt("answer_system", self.cfg), "cache_control": {"type": "ephemeral"}}]
        return system, [*prep.history, {"role": "user", "content": user_prompt}]

    def _postcheck(self, prep: Prepared, raw: str) -> cit.PostCheckResult:
        with tracing.span("answer.postcheck") as sp:
            pc = cit.postcheck(raw, {s.sid: s for s in prep.sources}, min_coverage=self.cfg.citation.min_sentence_coverage,
                               insufficient_message=self.cfg.citation.insufficient_message)
            if pc.ok and prep.conflicts:
                numbers = {c.record_id: c.n for c in pc.citations}
                pc.answer, appended = ensure_conflict_disclosure(pc.answer, prep.conflicts, numbers)
                if appended:
                    prep.dtrace.degrade("Câu trả lời của model chưa nêu đủ các giá trị mâu thuẫn -> hệ thống tự bổ sung danh sách đầy đủ")
            sp.update(output={"ok": pc.ok, "citations": len(pc.citations), "invalid": pc.invalid_sids,
                              "coverage": round(pc.coverage, 2), "reason": pc.reason})
        return pc

    # ================================================================== FINALIZE
    def _finalize(self, prep: Prepared, *, status: str, answer: str, pc: cit.PostCheckResult | None = None,
                  llm_result: Any = None, error: str | None = None) -> QAResponse:
        req, dtrace = prep.req, prep.dtrace
        used = set(pc.used_sids) if pc and pc.ok else set()
        dtrace.found = [src.to_found(src.sid in used) for src in prep.sources]
        dtrace.basis = [f"[{c.n}] {c.label}" + (f" (hiệu lực {c.effective_date})" if c.effective_date else "")
                        for c in (pc.citations if pc and pc.ok else [])]
        coverage = pc.coverage if pc and pc.ok else 0.0
        conf = compute_confidence(top_score=prep.top_score, score_kind=prep.score_kind, coverage=coverage,
                                  conflicts=len(prep.conflicts), degraded=len(dtrace.degraded),
                                  graph_paths=len(prep.graph_paths)) if status in ("answered", "pending_review") else \
            {"level": "không áp dụng", "score": None, "explanation": status}
        dtrace.confidence = conf
        dtrace.outcome = {"answered": "Đã trả lời có trích dẫn", "abstained": "Không trả lời: không đủ căn cứ, đề nghị chuyển người phụ trách",
                          "no_citation": "Không trả lời: bản nháp của model không có trích dẫn hợp lệ",
                          "pending_review": "Chờ supervisor duyệt (câu hỏi liên quan tiền)",
                          "error": "Lỗi hệ thống — đã trả thông báo thân thiện"}.get(status, status)
        trace_dict = dtrace.to_dict()
        citations = [c.to_dict() for c in pc.citations] if pc and pc.ok else []
        call = llm_result.call if llm_result is not None else None
        review_id = message_id = None
        user_answer = answer
        with self.sm() as s:
            if status == "pending_review":
                user_answer = self.cfg.review.pending_message
            if req.channel == "chat" and prep.session_id:
                sess = self.sessions.get_owned(s, prep.session_id, req.principal.subject)
                msg = self.sessions.add_message(
                    s, sess, "assistant", user_answer, trace_id=prep.trace_id,
                    status={"pending_review": "pending_review", "error": "error"}.get(status, "final"),
                    payload={"citations": citations if status != "pending_review" else [], "status": status})
                message_id = msg.id
            if status == "pending_review":
                item = enqueue_review(s, trace_id=prep.trace_id, session_id=prep.session_id, message_id=message_id,
                                      user_id=req.principal.subject, store_code=req.store_code, question=req.question,
                                      draft_answer=answer, citations=citations, decision_trace=trace_dict,
                                      reason="Câu hỏi liên quan tiền: " + ", ".join(prep.money_hits))
                review_id = item.id
            if status in ("abstained", "no_citation"):
                record_abstention(s, trace_id=prep.trace_id, question=req.question,
                                  reason=(prep.abstain.reason if status == "abstained" and prep.abstain else "no_citation") or status,
                                  top_score=prep.top_score, question_type=prep.route.question_type if prep.route else None,
                                  store_code=req.store_code, user_id=req.principal.subject)
            if prep.conflicts:
                persist_conflicts(s, prep.conflicts)
            s.add(QATrace(
                trace_id=prep.trace_id, channel=req.channel, session_id=prep.session_id, user_id=req.principal.subject,
                role=req.principal.role, store_code=req.store_code, question=req.question,
                question_type=prep.route.question_type if prep.route else None, route=prep.route.route if prep.route else None,
                status=status, answer=answer, citations=citations, decision_trace=trace_dict,
                conflicts=[c.to_dict() for c in prep.conflicts], confidence=conf.get("score"), top_score=prep.top_score,
                config_version=self.cfg.config_version, config_hash=self.cfg.config_hash,
                latency_ms=int((time.perf_counter() - prep.started) * 1000),
                cost_usd=call.cost_usd if call else 0.0, input_tokens=call.input_tokens if call else 0,
                output_tokens=call.output_tokens if call else 0,
                langfuse_trace_id=prep.trace_id if (tracing.current_trace() and tracing.current_trace().enabled) else None,
                error=error))
            s.commit()
        with tracing.span("answer.final", status=status) as sp:  # E1: span câu trả lời cuối cùng gửi user
            sp.update(output={"status": status, "answer": user_answer[:2000], "citations": len(citations),
                              "conflicts": len(prep.conflicts), "review_id": review_id})
        th = tracing.current_trace()
        if th is not None:
            th.update(output={"status": status, "answer": user_answer[:2000]},
                      metadata={"status": status, "confidence": conf.get("level"),
                                "question_type": prep.route.question_type if prep.route else None})
        log_event(logger, "qa_done", status=status, question_type=prep.route.question_type if prep.route else None,
                  route=prep.route.route if prep.route else None, sources=len(prep.sources),
                  citations=len(citations), conflicts=len(prep.conflicts),
                  latency_ms=int((time.perf_counter() - prep.started) * 1000))
        return QAResponse(
            trace_id=prep.trace_id, status=status, answer=user_answer, citations=citations if status != "pending_review" else [],
            conflicts=[c.to_dict() for c in prep.conflicts], decision_trace=trace_dict,
            question_type=prep.route.question_type if prep.route else None, route=prep.route.route if prep.route else None,
            confidence=conf, review_id=review_id, session_id=prep.session_id, message_id=message_id,
            trace_url=tracing.trace_url(prep.trace_id) if th is not None and th.enabled else None)

    def _error_response(self, req: QARequest, trace_id: str, prep: Prepared | None, exc: BaseException) -> QAResponse:
        msg = exc.message if isinstance(exc, AppError) and exc.http_status == 503 else ERROR_MESSAGE.format(trace_id=trace_id)
        if isinstance(exc, AppError) and exc.http_status == 503:
            msg = f"{msg} (Mã tra cứu: {trace_id})"
        prep = prep or Prepared(trace_id=trace_id, req=req, dtrace=DecisionTrace())
        prep.dtrace.degrade(f"{type(exc).__name__}: {str(exc)[:200]}")
        try:
            return self._finalize(prep, status="error", answer=msg, error=f"{type(exc).__name__}: {exc}"[:2000])
        except Exception:  # DB cũng lỗi -> vẫn trả lời thân thiện
            logger.exception("không lưu được trace lỗi")
            return QAResponse(trace_id=trace_id, status="error", answer=msg, session_id=prep.session_id)

    def _trace_meta(self, req: QARequest) -> dict[str, Any]:
        return {"store_code": req.store_code, "user_id": req.principal.subject, "role": req.principal.role,
                "session_id": req.session_id, "channel": req.channel}

    # ================================================================== ONE-SHOT
    def answer(self, req: QARequest) -> QAResponse:
        trace_id = new_id()
        with log_context(trace_id=trace_id, user_id=req.principal.subject, store_code=req.store_code,
                         session_id=req.session_id, config_version=self.cfg.config_version), \
                tracing.start_trace(f"qa.{req.channel}", trace_id=trace_id, user_id=req.principal.subject,
                                    session_id=req.session_id, input=req.question, metadata=self._trace_meta(req)):
            prep: Prepared | None = None
            try:
                prep = self._prepare(req, trace_id)
                if prep.abstain and prep.abstain.abstain:
                    return self._finalize(prep, status="abstained", answer=self.cfg.abstention.handoff_message)
                system, messages = self._messages(prep)
                with tracing.span("model.answer", model=self.llm.model_for("answer")):
                    res = self.llm.complete("answer", system=system, messages=messages,
                                            max_tokens=self.cfg.llm.answer_max_tokens)
                pc = self._postcheck(prep, res.text)
                if not pc.ok:
                    return self._finalize(prep, status="no_citation", answer=pc.answer, pc=pc, llm_result=res)
                status = "pending_review" if prep.money else "answered"
                return self._finalize(prep, status=status, answer=pc.answer, pc=pc, llm_result=res)
            except (LLMUnavailable, RetrievalUnavailable) as e:
                log_event(logger, "qa_upstream_unavailable", level=logging.ERROR, error=str(e))
                return self._error_response(req, trace_id, prep, e)
            except AppError as e:
                if e.http_status < 500:
                    raise  # lỗi nghiệp vụ (403 session người khác...) -> API trả đúng mã
                logger.exception("qa lỗi AppError")
                return self._error_response(req, trace_id, prep, e)
            except Exception as e:
                logger.exception("qa lỗi không lường trước")
                return self._error_response(req, trace_id, prep, e)

    # ================================================================== STREAMING (B8)
    def stream(self, req: QARequest) -> Iterator[tuple[str, dict[str, Any]]]:
        """Sinh (event, data): meta -> delta* -> [replace] -> citations -> conflicts -> trace -> done.
        Citation + decision trace gửi SAU CÙNG khi text đã stream xong. Text chỉ bắt đầu chảy khi đã
        thấy citation hợp lệ đầu tiên (StreamingCitationGate); post-check cuối cùng quyết định: không đạt
        -> event replace bằng thông báo không đủ căn cứ."""
        trace_id = new_id()
        with log_context(trace_id=trace_id, user_id=req.principal.subject, store_code=req.store_code,
                         session_id=req.session_id, config_version=self.cfg.config_version), \
                tracing.start_trace(f"qa.{req.channel}", trace_id=trace_id, user_id=req.principal.subject,
                                    session_id=req.session_id, input=req.question, metadata=self._trace_meta(req)):
            prep: Prepared | None = None
            try:
                prep = self._prepare(req, trace_id)
                yield "meta", {"trace_id": trace_id, "session_id": prep.session_id,
                               "question_type": prep.route.question_type if prep.route else None,
                               "route": prep.route.route if prep.route else None}
                if prep.abstain and prep.abstain.abstain:
                    resp = self._finalize(prep, status="abstained", answer=self.cfg.abstention.handoff_message)
                    yield "delta", {"text": resp.answer}
                    yield from self._tail(resp)
                    return
                system, messages = self._messages(prep)
                if prep.money:
                    with tracing.span("model.answer", model=self.llm.model_for("answer"), streaming=False):
                        res = self.llm.complete("answer", system=system, messages=messages,
                                                max_tokens=self.cfg.llm.answer_max_tokens)
                    pc = self._postcheck(prep, res.text)
                    status = "pending_review" if pc.ok else "no_citation"
                    resp = self._finalize(prep, status=status, answer=pc.answer, pc=pc, llm_result=res)
                    if status == "pending_review":
                        yield "pending_review", {"review_id": resp.review_id, "message": resp.answer,
                                                 "message_id": resp.message_id}
                    yield "delta", {"text": resp.answer}
                    yield from self._tail(resp)
                    return
                gate = cit.StreamingCitationGate({src.sid for src in prep.sources})
                with tracing.span("model.answer", model=self.llm.model_for("answer"), streaming=True) as sp:
                    handle = self.llm.stream("answer", system=system, messages=messages,
                                             max_tokens=self.cfg.llm.answer_max_tokens)
                    for piece in handle:
                        out = gate.feed(piece)
                        if out:
                            yield "delta", {"text": out}
                    tail = gate.flush()
                    if tail:
                        yield "delta", {"text": tail}
                    sp.update(output={"chars": len(gate.emitted), "gate_opened": gate.opened})
                res = handle.result
                pc = self._postcheck(prep, res.text if res else "")
                status = "answered" if pc.ok else "no_citation"
                resp = self._finalize(prep, status=status, answer=pc.answer, pc=pc, llm_result=res)
                if resp.answer.strip() != gate.emitted.strip():
                    yield "replace", {"text": resp.answer}
                yield from self._tail(resp)
            except (LLMUnavailable, RetrievalUnavailable) as e:
                log_event(logger, "qa_upstream_unavailable", level=logging.ERROR, error=str(e))
                resp = self._error_response(req, trace_id, prep, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}
            except AppError as e:
                if e.http_status < 500:
                    yield "error", {"message": e.message, "code": e.code, "trace_id": trace_id}
                    yield "done", {"status": "error", "trace_id": trace_id}
                    return
                logger.exception("qa stream lỗi AppError")
                resp = self._error_response(req, trace_id, prep, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}
            except Exception as e:
                logger.exception("qa stream lỗi không lường trước")
                resp = self._error_response(req, trace_id, prep, e)
                yield "error", {"message": resp.answer, "trace_id": trace_id}
                yield "done", {"status": "error", "trace_id": trace_id}

    @staticmethod
    def _tail(resp: QAResponse) -> Iterator[tuple[str, dict[str, Any]]]:
        yield "citations", {"citations": resp.citations}
        yield "conflicts", {"conflicts": resp.conflicts}
        yield "trace", {"decision_trace": resp.decision_trace, "confidence": resp.confidence, "trace_url": resp.trace_url}
        yield "done", {"status": resp.status, "trace_id": resp.trace_id, "answer": resp.answer,
                       "review_id": resp.review_id, "session_id": resp.session_id, "message_id": resp.message_id}


def _filters_vi(f: Filters | None) -> dict[str, Any]:
    if f is None:
        return {}
    out: dict[str, Any] = {}
    if f.store_code is not None:
        out["cửa hàng"] = " + ".join(s or "toàn hệ thống" for s in f.store_code)
    if f.as_of:
        out["có hiệu lực vào ngày"] = f.as_of
    elif f.is_active:
        out["trạng thái"] = "chỉ dữ liệu đang hiệu lực"
    if f.doc_type:
        out["loại tài liệu"] = f.doc_type
    return out
