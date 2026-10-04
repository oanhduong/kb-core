"""Integration assistant: model giả lập dùng tool (stream API của Anthropic SDK) -> tool thật chạy trên KB/bảng/đồ thị
của workspace -> citation post-check, artifact chart, review theo pack, abstention, SSE, lưu trace."""
import json
import re
from types import SimpleNamespace

import pytest


class _Stream:
    """Giả lập anthropic messages.stream(): text_stream + get_final_message() với content block text/tool_use."""

    def __init__(self, blocks, usage=(120, 60)):
        self.blocks = blocks
        self.usage = usage

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        text = "".join(b["text"] for b in self.blocks if b["type"] == "text")
        return [text[i:i + 9] for i in range(0, len(text), 9)]

    def get_final_message(self):
        content = []
        for i, b in enumerate(self.blocks):
            if b["type"] == "text":
                content.append(SimpleNamespace(type="text", text=b["text"], model_dump=lambda b=b: b))
            else:
                blk = {"type": "tool_use", "id": f"tu_{i}", "name": b["name"], "input": b["input"]}
                content.append(SimpleNamespace(type="tool_use", id=blk["id"], name=b["name"], input=b["input"], model_dump=lambda blk=blk: blk))
        return SimpleNamespace(content=content, stop_reason="tool_use" if any(b["type"] == "tool_use" for b in self.blocks) else "end_turn",
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


class AgentModel:
    """Chính sách: câu hỏi chứa 'bảng'/'bao nhiêu SKU' -> list_tables rồi query_tables; 'liên quan' -> explore_graph;
    'tài liệu gì' -> list_documents; 'xin chào' -> trả lời chung không tool; còn lại -> search_documents.
    Lượt cuối: trích câu đầu của nguồn S1 kèm [S1] (và [S2] nếu có)."""

    def __init__(self):
        self.messages = self
        self.calls = []

    def _last_user(self, msgs):
        for m in reversed(msgs):
            if m["role"] == "user" and isinstance(m["content"], str):
                return m["content"]
        return ""

    def _tool_results(self, msgs):
        last = msgs[-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            return "\n".join(c["content"] for c in last["content"] if c.get("type") == "tool_result")
        return None

    def stream(self, **kwargs):
        msgs = kwargs["messages"]
        q = self._last_user(msgs).lower()
        results = self._tool_results(msgs)
        self.calls.append(("turn", q[:40]))
        if results is None:  # lượt đầu
            if "xin chào" in q:
                return _Stream([{"type": "text", "text": "Xin chào! Tôi có thể tra cứu tài liệu, phân tích số liệu và vẽ biểu đồ cho bạn."}])
            if "bảng" in q or "bao nhiêu sku" in q:
                return _Stream([{"type": "text", "text": "Để tôi xem catalog."}, {"type": "tool_use", "name": "list_tables", "input": {}}])
            if "liên quan" in q:
                return _Stream([{"type": "tool_use", "name": "explore_graph", "input": {"entity": "GreenMart", "depth": 1}}])
            if "tài liệu gì" in q:
                return _Stream([{"type": "tool_use", "name": "list_documents", "input": {}}])
            if "không tồn tại" in q:
                return _Stream([{"type": "tool_use", "name": "search_documents", "input": {"query": "zzz qqq xxx", "top_k": 3}}])
            return _Stream([{"type": "tool_use", "name": "search_documents", "input": {"query": self._last_user(msgs), "top_k": 4}}])
        # có kết quả tool
        if "<catalog>" in results:
            m = re.search(r"- (\w+) \(\d+ dòng\) — Bảng giá", results)
            t = m.group(1) if m else "bang_gia"
            return _Stream([{"type": "tool_use", "name": "query_tables",
                             "input": {"sql": f"SELECT category, count(*) AS so_sku FROM {t} GROUP BY category ORDER BY so_sku DESC",
                                       "purpose": "Số SKU theo ngành hàng", "chart": {"type": "bar", "x": "category", "y": "so_sku", "title": "SKU theo ngành"}}}])
        if "Không tìm thấy đoạn nào" in results:  # kể cả trường hợp điểm liên quan quá thấp
            return _Stream([{"type": "text", "text": "Tôi không tìm thấy thông tin về nội dung này trong các tài liệu hiện có."}])
        sids = re.findall(r"\[(S\d+)\]", results)
        first_src = re.search(r"\[S1\][^\n]*\n<untrusted_document[^>]*>\n([^\n]+)", results)
        line = (first_src.group(1) if first_src else "Dữ liệu đã tìm được")[:150]
        text = f"Theo tài liệu: {line} [S1]."
        if "Truy vấn số liệu" in results:
            rows = re.findall(r"\n([^|\n]+) \| (\d+)", results)
            text = "Số SKU theo ngành hàng: " + ", ".join(f"{a.strip()} {b}" for a, b in rows[:3]) + " [S1]."
        elif len(set(sids)) > 1:
            text += f" Ngoài ra còn quy định liên quan [{sorted(set(sids))[1]}]."
        return _Stream([{"type": "text", "text": text}])

    def create(self, **kwargs):  # cho compaction/nl2sql... không dùng ở test này
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def overlap_rerank(query, texts):
    q = set(re.findall(r"\w+", query.lower()))
    return [4.0 * len(q & set(re.findall(r"\w+", t.lower()))) / max(1, len(q)) - 1.0 for t in texts]


@pytest.fixture(scope="module")
def svc(seeded):
    from core.config import get_config
    from core.db import get_sessionmaker
    from core.llm import LLMClient
    from assistant.agent import AssistantService
    from index.bm25_cache import BM25Cache
    from index.embeddings import get_embedder
    from index.kb_store import KBVectorStore
    from qa.retrieval import VectorRetriever

    cfg = get_config().model_copy(deep=True)
    cfg.retrieval.rerank.enabled = True
    cfg.abstention.rerank_min_score = 0.3  # overlap_rerank: không trùng token nào -> logit -1 -> sigmoid 0.27 < 0.3
    sm = get_sessionmaker()
    emb = get_embedder(cfg)
    fake = AgentModel()
    llm = LLMClient(cfg=cfg, client=fake, sleep=lambda s: None)
    retr = VectorRetriever(cfg, KBVectorStore(sm, emb.model_name, emb.dim), BM25Cache(sm, 0).get, emb, rerank_fn=overlap_rerank)
    return AssistantService(cfg, sm, llm, retr), fake, seeded["workspace_id"]


def ctx(ws, role="viewer", store="GM-HN01"):
    from core.principal import Principal, WorkspaceContext

    return WorkspaceContext(ws, "Test retail", "retail", role, {"store_code": store} if store else {}, {}, Principal(subject=f"a-{role}"))


def req(svc_tuple, q, role="viewer", **kw):
    from assistant.agent import AssistantRequest

    svc, _, ws = svc_tuple
    c = ctx(ws, role)
    return AssistantRequest(question=q, ctx=c, scope=c.scope_value, **kw)


def test_search_tool_answer_with_citations_and_trace(svc):
    s, fake, ws = svc
    r = s.answer(req(svc, "Thời hạn đổi trả theo ngành hàng được quy định thế nào?", channel="ask"))
    assert r.status == "answered", r.decision_trace
    assert r.citations and "[1]" in r.answer and r.intent == "qa"
    assert [t["tool"] for t in r.tool_calls] == ["search_documents"]
    dt = r.decision_trace
    assert dt["searched"][0]["tool"] == "search_documents" and any(f["used_in_answer"] for f in dt["found"])
    assert dt["understood"]["question_type"] == "qa" and dt["confidence"]["level"] in ("cao", "trung bình", "thấp")
    assert dt["summary"][0].startswith("Đã hiểu")


def test_analytics_tools_produce_chart_artifact(svc):
    s, fake, ws = svc
    r = s.answer(req(svc, "Mỗi ngành hàng có bao nhiêu SKU trong bảng giá?", role="editor", channel="ask"))
    assert r.status == "answered", r.decision_trace
    assert [t["tool"] for t in r.tool_calls] == ["list_tables", "query_tables"] and r.intent == "analytics"
    art = [a for a in r.artifacts if a["type"] == "query_result"][0]
    assert art["row_count"] == 8 and art["chart"]["type"] == "bar" and art["sql"].lower().startswith("select")
    assert r.citations[0]["label"].startswith("Truy vấn số liệu") and "số liệu tính trực tiếp bằng SQL" in r.confidence["explanation"]


def test_general_chat_without_tools_is_allowed(svc):
    s, fake, ws = svc
    r = s.answer(req(svc, "Xin chào, bạn làm được gì?", channel="ask"))
    assert r.status == "answered_general" and not r.citations and r.intent == "general"


def test_no_results_becomes_abstention(svc):
    from core.db import session_scope
    from core.models import Abstention
    from sqlalchemy import select

    s, fake, ws = svc
    r = s.answer(req(svc, "Thông tin không tồn tại về bảo hiểm xe máy?", channel="ask"))
    assert r.status == "abstained"
    with session_scope() as db:
        assert db.execute(select(Abstention).where(Abstention.trace_id == r.trace_id)).scalar_one().reason == "no_results"


def test_money_question_from_viewer_goes_to_review(svc):
    s, fake, ws = svc
    r = s.answer(req(svc, "Giá bán của ĐT-018 là bao nhiêu?", channel="chat"))
    assert r.status == "pending_review" and r.review_id and r.citations == [] and r.message_id


def test_stream_event_order_status_artifact_and_gate(svc):
    s, fake, ws = svc
    events = list(s.stream(req(svc, "Mỗi ngành hàng có bao nhiêu SKU trong bảng giá?", role="editor", channel="chat")))
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-3:] == ["citations", "trace", "done"]
    assert names.index("status") < names.index("artifact") < names.index("citations")
    deltas = "".join(d["text"] for e, d in events if e == "delta")
    done = events[-1][1]
    assert "Để tôi xem catalog" not in deltas  # text trung gian trước khi có nguồn không được phát
    assert done["status"] == "answered" and "[1]" in done["answer"] and done["artifacts"][0]["type"] == "query_result"
    final_text = done["answer"]
    assert deltas.strip() == final_text.strip() or any(e == "replace" for e in names)


def test_model_failure_gives_friendly_error_and_trace(svc):
    import anthropic
    import httpx

    s, fake, ws = svc

    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def stream(self, **kw):
            raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
    from core.llm import LLMClient

    s2 = s.__class__(s.cfg, s.sm, LLMClient(cfg=s.cfg, client=Boom(), sleep=lambda x: None), s.retriever)
    r = s2.answer(req(svc, "Thời hạn đổi trả?", channel="ask"))
    assert r.status == "error" and r.trace_id in r.answer
