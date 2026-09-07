"""Test cho qa/answer.py — FakeRetriever/FakeClient tiêm sẵn (cùng phong cách dependency-injection với
tests/test_reindex.py, tests/test_chunkers.py FakeClient), không cần Postgres/Qdrant/Anthropic API
thật. Phần cuối file test FastAPI endpoint /ask qua TestClient + app.dependency_overrides."""
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from ingest.chunkers import Chunk
from index.store import Filters
from qa.answer import (
    NO_ANSWER_TEXT,
    ChunkLookup,
    Source,
    _resolve_parent_chunks,
    answer_question,
    app,
    configure_app,
    get_chunk_lookup,
    get_client,
    get_retriever,
)


# =====================================================================================
# FIXTURES CHUNG
# =====================================================================================

def _parent_chunk(chunk_id, text, location="p.1"):
    return Chunk(
        chunk_id=chunk_id, parent_id="r1", text=text, location=location,
        metadata={"chunk_role": "parent", "source_record_id": "r1"},
    )


def _child_chunk(chunk_id, parent_id, text, location="p.1"):
    return Chunk(
        chunk_id=chunk_id, parent_id=parent_id, text=text, location=location,
        metadata={"chunk_role": "child", "source_record_id": "r1"},
    )


@dataclass
class FakeSearchResult:
    id: str
    text: str = ""
    metadata: dict = field(default_factory=dict)


class FakeRetriever:
    def __init__(self, results):
        self._results = results
        self.calls = []

    def search(self, query, top_k, filters=None):
        self.calls.append((query, top_k, filters))
        return self._results[:top_k]


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def fake_response(text):
    return SimpleNamespace(content=[text_block(text)], usage=SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0,
    ))


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages hết response đã script.")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


# =====================================================================================
# ChunkLookup / _resolve_parent_chunks
# =====================================================================================

def test_chunk_lookup_get_returns_none_for_missing_id():
    lookup = ChunkLookup([_parent_chunk("p1", "nội dung")])
    assert lookup.get("missing") is None


def test_resolve_parent_chunks_maps_child_to_parent():
    parent = _parent_chunk("p1", "Nội dung đầy đủ của parent chunk.")
    child = _child_chunk("p1#c0000", "p1", "đoạn ngắn")
    lookup = ChunkLookup([parent, child])

    resolved = _resolve_parent_chunks([FakeSearchResult(id="p1#c0000")], lookup)

    assert resolved == [parent]


def test_resolve_parent_chunks_dedupes_same_parent_from_multiple_children():
    parent = _parent_chunk("p1", "Nội dung parent")
    child_a = _child_chunk("p1#c0000", "p1", "đoạn a")
    child_b = _child_chunk("p1#c0001", "p1", "đoạn b")
    lookup = ChunkLookup([parent, child_a, child_b])

    resolved = _resolve_parent_chunks(
        [FakeSearchResult(id="p1#c0000"), FakeSearchResult(id="p1#c0001")], lookup,
    )

    assert resolved == [parent]


def test_resolve_parent_chunks_skips_unresolvable_child_without_raising():
    parent = _parent_chunk("p1", "Nội dung parent")
    lookup = ChunkLookup([parent])

    resolved = _resolve_parent_chunks([FakeSearchResult(id="unknown-child")], lookup)

    assert resolved == []


def test_resolve_parent_chunks_skips_child_whose_parent_is_missing():
    child = _child_chunk("p1#c0000", "missing-parent", "đoạn a")
    lookup = ChunkLookup([child])

    resolved = _resolve_parent_chunks([FakeSearchResult(id="p1#c0000")], lookup)

    assert resolved == []


def test_resolve_parent_chunks_preserves_order():
    parent_a = _parent_chunk("pa", "A")
    parent_b = _parent_chunk("pb", "B")
    child_a = _child_chunk("pa#c0", "pa", "a")
    child_b = _child_chunk("pb#c0", "pb", "b")
    lookup = ChunkLookup([parent_a, parent_b, child_a, child_b])

    resolved = _resolve_parent_chunks(
        [FakeSearchResult(id="pb#c0"), FakeSearchResult(id="pa#c0")], lookup,
    )

    assert [c.chunk_id for c in resolved] == ["pb", "pa"]


# =====================================================================================
# answer_question
# =====================================================================================

def _lookup_with_one_pair(parent_text="Chính sách đổi trả trong 7 ngày."):
    parent = _parent_chunk("p1", parent_text, location="Điều 5")
    child = _child_chunk("p1#c0000", "p1", "đổi trả 7 ngày")
    return ChunkLookup([parent, child]), child


def test_answer_question_returns_answer_and_sources():
    lookup, child = _lookup_with_one_pair()
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("Bạn được đổi trả trong 7 ngày.")])

    result = answer_question("Chính sách đổi trả thế nào?", "HCM01", retriever, lookup, client=client)

    assert result.answer == "Bạn được đổi trả trong 7 ngày."
    assert result.sources == [Source(chunk_id="p1", location="Điều 5", text="Chính sách đổi trả trong 7 ngày.")]


def test_answer_question_no_results_returns_fixed_message_without_calling_llm():
    lookup = ChunkLookup([])
    retriever = FakeRetriever([])
    client = FakeClient([])  # không script response nào -> lỗi ngay nếu code lỡ gọi LLM

    result = answer_question("Câu hỏi bất kỳ", None, retriever, lookup, client=client)

    assert result.answer == NO_ANSWER_TEXT
    assert result.sources == []
    assert len(client.messages.calls) == 0


def test_answer_question_filters_by_store_code():
    lookup, child = _lookup_with_one_pair()
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("ok")])

    answer_question("hỏi gì đó", "HCM01", retriever, lookup, client=client)

    query, top_k, filters = retriever.calls[0]
    assert filters == Filters(store_code="HCM01", is_active=True)


def test_answer_question_without_store_code_only_filters_is_active():
    lookup, child = _lookup_with_one_pair()
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("ok")])

    answer_question("hỏi gì đó", None, retriever, lookup, client=client)

    _, _, filters = retriever.calls[0]
    assert filters == Filters(is_active=True)


def test_answer_question_wraps_chunk_text_as_untrusted_in_prompt():
    lookup, child = _lookup_with_one_pair(parent_text="Nội dung nhạy cảm từ tài liệu khách hàng.")
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("ok")])

    answer_question("hỏi gì đó", None, retriever, lookup, client=client)

    call = client.messages.calls[0]
    user_content = call["messages"][0]["content"]
    assert "<untrusted_document" in user_content
    assert "Nội dung nhạy cảm từ tài liệu khách hàng." in user_content
    assert "</untrusted_document>" in user_content


def test_answer_question_system_prompt_includes_provenance_instructions():
    lookup, child = _lookup_with_one_pair()
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("ok")])

    answer_question("hỏi gì đó", None, retriever, lookup, client=client)

    from security.provenance import PROVENANCE_SYSTEM_INSTRUCTIONS

    call = client.messages.calls[0]
    assert PROVENANCE_SYSTEM_INSTRUCTIONS in call["system"]


def test_answer_question_top_k_passed_to_retriever():
    lookup, child = _lookup_with_one_pair()
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("ok")])

    answer_question("hỏi gì đó", None, retriever, lookup, client=client, top_k=3)

    _, top_k, _ = retriever.calls[0]
    assert top_k == 3


# =====================================================================================
# FastAPI /ask
# =====================================================================================

@pytest.fixture
def test_client():
    fastapi_testing = pytest.importorskip("fastapi.testclient")
    lookup, child = _lookup_with_one_pair(parent_text="Giá SKU ABC-123 là 590.000đ.")
    retriever = FakeRetriever([FakeSearchResult(id=child.chunk_id)])
    client = FakeClient([fake_response("Giá là 590.000đ.")])
    configure_app(retriever, lookup, client=client)
    try:
        yield fastapi_testing.TestClient(app), retriever, client
    finally:
        app.dependency_overrides.clear()
        configure_app(None, None, client=None)


def test_ask_endpoint_returns_answer_and_sources(test_client):
    client, _retriever, _fake_llm = test_client

    response = client.post("/ask", json={"question": "Giá SKU ABC-123?", "store_code": "HCM01"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Giá là 590.000đ."
    assert body["sources"] == [{"chunk_id": "p1", "location": "Điều 5", "text": "Giá SKU ABC-123 là 590.000đ."}]


def test_ask_endpoint_passes_store_code_filter(test_client):
    client, retriever, _fake_llm = test_client

    client.post("/ask", json={"question": "hỏi gì đó", "store_code": "HN01"})

    _, _, filters = retriever.calls[0]
    assert filters.store_code == "HN01"


def test_ask_endpoint_store_code_optional(test_client):
    client, _retriever, _fake_llm = test_client

    response = client.post("/ask", json={"question": "hỏi gì đó"})

    assert response.status_code == 200


def test_get_retriever_raises_clear_error_when_not_configured():
    configure_app(None, None, client=None)
    with pytest.raises(RuntimeError):
        get_retriever()
    with pytest.raises(RuntimeError):
        get_chunk_lookup()
    assert get_client() is None
