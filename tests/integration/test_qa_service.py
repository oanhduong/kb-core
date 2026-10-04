"""Integration Phần B: QAService trên KB/graph thật (Postgres + pgvector + AGE), model giả lập
(không gọi API thật) — kiểm luồng router -> graph/vector -> parent chunk -> model -> post-check
citation -> review -> abstention -> SSE -> degradation."""
import re
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import select

class FakeModel:
    """Trả lời bằng cách trích câu đầu của nguồn S1 kèm [S1] (hoặc theo `mode`)."""

    def __init__(self, mode="cite"):
        self.mode = mode
        self.calls = 0
        self.answer_calls = 0  # chỉ lần gọi model TRẢ LỜI (prompt có khối <sources>)
        self.messages = self

    def _answer(self, kwargs):
        if self.mode == "timeout":
            raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
        user = kwargs["messages"][-1]["content"]
        if self.mode == "no_cite":
            return "Theo tài liệu thì thời hạn là 30 ngày cho mọi mặt hàng."
        m = re.search(r'<source id="S1"[^>]*>\s*<untrusted_document[^>]*>\n(.*?)\n</untrusted_document>', user, re.S)
        first = (m.group(1) if m else "không rõ").split("\n")[0][:160]
        return f"Theo tài liệu: {first} [S1]."

    def create(self, **kwargs):
        self.calls += 1
        if "<sources>" in str(kwargs["messages"][-1]["content"]):
            self.answer_calls += 1
        text = self._answer(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))

    def stream(self, **kwargs):
        self.calls += 1
        text = self._answer(kwargs)
        final = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                                usage=SimpleNamespace(input_tokens=100, output_tokens=20))

        class _S:
            text_stream = [text[i:i + 7] for i in range(0, len(text), 7)]

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def get_final_message(self_inner):
                return final
        return _S()


def overlap_rerank(query, texts):
    q = set(re.findall(r"\w+", query.lower()))
    return [4.0 * len(q & set(re.findall(r"\w+", t.lower()))) / max(1, len(q)) - 1.0 for t in texts]


WS: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _ws(seeded):
    WS["id"] = seeded["workspace_id"]
    return seeded


def make_service(mode="cite", rerank=overlap_rerank, graph=True, cfg_mut=None):
    from core.config import get_config
    from core.db import get_sessionmaker
    from core.llm import LLMClient
    from graph.store import AgeGraphStore, get_graph_store
    from index.bm25_cache import BM25Cache
    from index.embeddings import get_embedder
    from index.kb_store import KBVectorStore
    from qa.answer import QAService
    from qa.retrieval import VectorRetriever

    cfg = get_config().model_copy(deep=True)
    cfg.retrieval.rerank.enabled = rerank is not None
    if cfg_mut:
        cfg_mut(cfg)
    sm = get_sessionmaker()
    emb = get_embedder(cfg)
    fake = FakeModel(mode)
    llm = LLMClient(cfg=cfg, client=fake, sleep=lambda s: None)
    retr = VectorRetriever(cfg, KBVectorStore(sm, emb.model_name, emb.dim), BM25Cache(sm, 0).get, emb, rerank_fn=rerank)
    provider = (lambda ws: get_graph_store(ws)) if graph else (lambda ws: AgeGraphStore("postgresql://x@127.0.0.1:1/none", connect_timeout=1))
    return QAService(cfg, sm, llm, retr, provider), fake


def staff(store="GM-HN01", role="viewer", pack="retail"):
    """Ngữ cảnh workspace test: viewer có scope store (nhân viên), editor = supervisor."""
    from core.principal import Principal, WorkspaceContext

    p = Principal(subject=f"u-{role}", email=f"{role}@test.local", name=role)
    return WorkspaceContext(WS["id"], "Test retail", pack, role, {"store_code": store} if store else {}, {}, p)


def req(q, principal=None, **kw):
    from qa.answer import QARequest

    ctx = principal or staff()
    return QARequest(question=q, ctx=ctx, store_code=ctx.scope_value, **kw)


def test_policy_lookup_answered_with_formatted_citation(seeded):
    from core.db import session_scope
    from core.models import QATrace

    svc, fake = make_service()
    r = svc.answer(req("Thời hạn đổi trả theo ngành hàng được quy định thế nào?"))
    assert r.status == "answered", r.decision_trace
    assert r.route == "vector" and r.citations and "[1]" in r.answer
    assert any("Điều" in c["label"] for c in r.citations)
    dt = r.decision_trace
    assert dt["understood"]["question_type"] == "lookup" and dt["summary"][0].startswith("Đã hiểu")
    assert any(f["used_in_answer"] for f in dt["found"]) and dt["confidence"]["level"] in ("cao", "trung bình", "thấp")
    with session_scope() as s:
        t = s.get(QATrace, r.trace_id)
        assert t.status == "answered" and t.config_version and t.store_code == "GM-HN01"


def test_money_question_goes_to_review_then_supervisor_override(seeded):
    from core.db import session_scope
    from core.models import ChatMessage, QATrace, ReviewItem
    from qa.review import approve

    svc, _ = make_service()
    r = svc.answer(req("Giá bán của ĐT-018 là bao nhiêu?", channel="chat"))
    assert r.status == "pending_review" and r.review_id and r.citations == []
    assert "supervisor duyệt" in r.answer
    with session_scope() as s:
        item = s.get(ReviewItem, r.review_id)
        assert item.status == "pending" and "[1]" in item.draft_answer and item.draft_citations
        approve(s, item.id, "sup-1", edited_answer="Giá ĐT-018 hiện là 1.440.000đ [1].", note="đã kiểm tra")
    with session_scope() as s:
        item = s.get(ReviewItem, r.review_id)
        assert item.status == "approved" and item.overridden and item.reviewer_id == "sup-1"
        msg = s.get(ChatMessage, r.message_id)
        assert msg.status == "final" and msg.content.startswith("Giá ĐT-018")
        assert s.get(QATrace, r.trace_id).status == "answered"


def test_supervisor_money_question_bypasses_review(seeded):
    svc, _ = make_service()
    r = svc.answer(req("Giá bán của ĐT-018 là bao nhiêu?", principal=staff(role="editor")))
    assert r.status == "answered" and r.citations


def test_answer_without_citation_is_blocked_and_recorded_as_abstention(seeded):
    from core.db import session_scope
    from core.models import Abstention

    svc, _ = make_service(mode="no_cite")
    r = svc.answer(req("Thời hạn đổi trả hàng điện tử bao lâu?"))
    assert r.status == "no_citation" and r.citations == []
    assert r.answer == svc.cfg.citation.insufficient_message
    with session_scope() as s:
        assert s.execute(select(Abstention).where(Abstention.trace_id == r.trace_id)).scalar_one().reason == "no_citation"


def test_low_rerank_score_abstains_without_calling_model(seeded):
    svc, fake = make_service(rerank=lambda q, texts: [-6.0] * len(texts))
    r = svc.answer(req("Chính sách bảo hiểm xe máy cho nhân viên là gì?"))
    assert r.status == "abstained" and fake.answer_calls == 0
    assert "supervisor" in r.answer.lower() or "phụ trách" in r.answer


def test_model_timeout_retries_then_friendly_error_never_raises(seeded):
    svc, fake = make_service(mode="timeout")
    r = svc.answer(req("Thời hạn đổi trả theo ngành hàng được quy định thế nào?"))
    assert r.status == "error" and r.trace_id in r.answer
    assert fake.answer_calls == svc.cfg.llm.max_retries + 1


def test_reranker_failure_degrades_to_hybrid(seeded):
    def boom(q, t):
        raise RuntimeError("model hỏng")

    svc, _ = make_service(rerank=boom, cfg_mut=lambda c: setattr(c.abstention, "fallback_min_dense_score", 0.0))
    r = svc.answer(req("Thời hạn đổi trả theo ngành hàng được quy định thế nào?"))
    assert r.status == "answered"
    assert any("Reranker lỗi" in d for d in r.decision_trace["degraded"])


def test_multi_hop_goes_through_graph_with_paths_in_trace(seeded):
    svc, _ = make_service()
    r = svc.answer(req("Loa ĐT-018 thuộc ngành hàng nào và đang được áp dụng khuyến mãi nào?"))
    assert r.route == "graph" and r.status == "answered", r.decision_trace
    assert r.decision_trace["graph_paths"] and any(f["origin"] == "graph" for f in r.decision_trace["found"])


def test_graph_unavailable_falls_back_to_vector(seeded):
    svc, _ = make_service(graph=False)
    r = svc.answer(req("Loa ĐT-018 thuộc ngành hàng nào và đang được áp dụng khuyến mãi nào?"))
    assert r.route == "vector" and r.status in ("answered", "abstained")
    assert any("Graph" in d for d in r.decision_trace["degraded"])


def test_aggregation_counts_with_computed_fact(seeded):
    svc, _ = make_service()
    r = svc.answer(req("Có bao nhiêu chương trình khuyến mãi áp dụng cho ngành hàng Điện máy?", principal=staff(role="editor")))
    assert r.question_type == "aggregation" and r.route == "graph_aggregation"
    assert r.decision_trace["computed_facts"] and "Số khuyến mãi" in r.decision_trace["computed_facts"][0]


def test_streaming_event_order_and_citation_gate(seeded):
    svc, _ = make_service()
    events = list(svc.stream(req("Thời hạn đổi trả theo ngành hàng được quy định thế nào?", channel="chat")))
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-4:] == ["citations", "conflicts", "trace", "done"]
    deltas = "".join(d["text"] for e, d in events if e == "delta")
    done = events[-1][1]
    assert done["status"] == "answered" and deltas.strip() == done["answer"].strip()
    assert "[1]" in deltas and "[S1]" not in deltas
    assert events[names.index("citations")][1]["citations"]


def test_conflicting_price_sources_are_surfaced_with_dates(seeded, tmp_path):
    from docx import Document as Docx

    from core.config import get_config
    from core.db import session_scope
    from core.models import Conflict
    from ingest.service import UploadOptions, register_upload
    from worker.main import Worker

    p = tmp_path / "poster_khuyen_mai_thang_10.docx"
    d = Docx()
    d.add_heading("POSTER KHUYẾN MÃI THÁNG 10", level=0)
    d.add_paragraph("Hiệu lực từ ngày 01/10/2026. Loa ĐT-018 giá chỉ còn 1.199.000đ tại toàn hệ thống.")
    d.save(p)
    with session_scope() as s:
        register_upload(s, get_config(), workspace_id=WS["id"], src=str(p), filename=p.name, opts=UploadOptions(doc_type="promotion"), uploaded_by="test")
    Worker("qa-test").loop(once=True)
    with session_scope() as s:
        assert s.execute(select(Conflict).where(Conflict.entity_key == "sku:ĐT-018|store:*", Conflict.status == "open")).first()
    svc, _ = make_service()
    r = svc.answer(req("Giá ĐT-018 hiện tại là bao nhiêu?", principal=staff(role="editor")))
    assert r.conflicts and {v["value"] for v in r.conflicts[0]["values"]} >= {1199000}
    assert "1.199.000đ" in r.answer and "MỚI HƠN" in r.answer  # code tự công bố đủ các giá trị + nguồn mới hơn


def test_chat_history_compaction(seeded):
    from core.db import session_scope
    from core.models import ChatSession

    svc, _ = make_service(cfg_mut=lambda c: (setattr(c.chat, "history_max_messages", 4), setattr(c.chat, "keep_recent_messages", 2)))
    sid = None
    for q in ["Điều kiện đổi trả chung là gì?", "Còn thời hạn đổi trả thì sao?", "Bảo hành áp dụng thế nào?", "Còn quy trình khiếu nại?"]:
        r = svc.answer(req(q, channel="chat", session_id=sid))
        sid = r.session_id
    with session_scope() as s:
        sess = s.get(ChatSession, sid)
        assert sess.summary and sess.summarized_upto_id  # đã nén phần cũ (LLM giả lập -> tóm tắt)
